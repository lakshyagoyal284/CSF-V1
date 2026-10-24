#!/usr/bin/env node
/**
 * End-to-end browser check for the AgriTrust dual portal.
 *
 * Requires the API on :8000 and the Next.js app on :3000:
 *   .venv/Scripts/python -m uvicorn main:app --port 8000
 *   cd frontend && npm run dev
 *
 * Then:
 *   cd frontend && node scripts/verify-portal.mjs
 *
 * It signs in as the demo banker, creates a demo file, previews a PDF proof,
 * verifies the evidence, runs the model, then signs in as a borrower and
 * submits a new application through the multi-step form.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";

import puppeteer from "puppeteer-core";

const CHROME =
  process.env.CHROME_PATH ??
  "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.PORTAL_URL ?? "http://127.0.0.1:3000";
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ARTIFACTS = path.resolve(SCRIPT_DIR, "../../.e2e");
const UPLOAD = path.join(ARTIFACTS, "agritrust_land_record.pdf");

const checks = [];
const consoleErrors = [];

function check(label, condition) {
  checks.push({ label, passed: Boolean(condition) });
  console.log(`${condition ? "PASS" : "FAIL"}  ${label}`);
}

async function waitText(page, text, timeout = 25000) {
  await page.waitForFunction(
    // innerText reflects CSS text-transform, so compare case-insensitively.
    (needle) => document.body.innerText.toLowerCase().includes(needle.toLowerCase()),
    { timeout },
    text,
  );
}

async function domClick(page, selector) {
  // Headless Chrome can lose document focus after a client-side route change,
  // which makes CDP mouse events no-ops. Dispatching the click through the DOM
  // still exercises React's real onClick/onChange handlers.
  const clicked = await page.evaluate((sel) => {
    const element = document.querySelector(sel);
    if (!element) return false;
    element.click();
    return true;
  }, selector);
  if (!clicked) throw new Error(`Element "${selector}" not found`);
}

async function clickButtonText(page, text) {
  const clicked = await page.evaluate((needle) => {
    const buttons = [...document.querySelectorAll("button")];
    const button = buttons.find(
      (element) => element.textContent?.trim().toLowerCase().includes(needle.toLowerCase()),
    );
    if (!button) return false;
    button.click();
    return true;
  }, text);
  if (!clicked) throw new Error(`Button containing "${text}" not found`);
}

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

try {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });
  const client = await page.createCDPSession();
  await client.send('Network.clearBrowserCookies');
  page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(`console: ${message.text()}`);
  });

  // ---------------------------------------------------------------- banker
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 90000 }).catch(async (err) => {
    console.log("first goto failed, retrying once:", err.message);
    await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 90000 });
  });
  await page.waitForSelector('[data-testid="demo-bank-officer"]', { timeout: 90000 });
  await new Promise((r) => setTimeout(r, 1000));
  check("Landing page renders both portals", await page.$('[data-testid="demo-farmer"]') !== null);

  await page.click('[data-testid="demo-bank-officer"]');
  await page.waitForFunction(
    () => (document.querySelector('input[type="email"]')?.value?.length ?? 0) > 0,
    { timeout: 5000 },
  );
  await page.click('[data-testid="auth-submit"]');
  await page.waitForFunction(() => window.location.pathname === "/banker", {
    timeout: 30000,
  });
  await waitText(page, "Underwriting desk");
  check("Banker login reaches the underwriting dashboard", true);

  await page.waitForSelector('[data-testid="load-demo"]');
  await domClick(page, '[data-testid="load-demo"]');
  await page.waitForSelector('[data-testid^="verify-field-"]', { timeout: 30000 });
  await waitText(page, "Ramesh Yadav");
  check("Demo application loads with declarations", true);

  // Preview the first PDF proof. The modal loads it with the bearer token.
  const previewButton = await page.$('[data-testid^="preview-doc-"]');
  check("Document proof is listed", previewButton !== null);
  if (previewButton) {
    await domClick(page, '[data-testid^="preview-doc-"]');
    await page.waitForSelector('[role="dialog"]', { timeout: 15000 });
    await page.waitForFunction(
      () => {
        const dialog = document.querySelector('[role="dialog"]');
        if (!dialog) return false;
        const frame = dialog.querySelector("iframe");
        return Boolean(frame?.getAttribute("src")?.startsWith("blob:"));
      },
      { timeout: 15000 },
    );
    check("PDF proof renders from an authenticated blob URL", true);
    await domClick(page, '[data-testid="preview-close"]');
    await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), {
      timeout: 5000,
    });
  }

  await page.evaluate(() => {
    const fieldCheckboxes = document.querySelectorAll('[data-testid^="verify-field-"]');
    fieldCheckboxes.forEach((cb) => cb.click());
    const docCheckboxes = document.querySelectorAll('[data-testid^="verify-doc-"]');
    docCheckboxes.forEach((cb) => cb.click());
  });
  check("Declarations and documents can be marked verified", true);

  await domClick(page, '[data-testid="calculate-score"]');
  await page.waitForSelector('[data-testid="score-panel"]', { timeout: 30000 });
  await waitText(page, "Why this score");
  const scoreLabel = await page.$eval(
    '[data-testid="score-panel"] svg[role="img"]',
    (element) => element.getAttribute("aria-label") ?? "",
  );
  check(`Credit score gauge renders (${scoreLabel.slice(0, 28)}…)`, /Credit score \d{3}/.test(scoreLabel));
  check(
    "Risk tier badge is shown",
    (await page.$eval('[data-testid="score-panel"]', (element) => element.innerText)).match(
      /Pre-Approved|Manual Review|High Risk/,
    ) !== null,
  );
  check(
    "Recommended limit is shown",
    (await page.$eval('[data-testid="score-panel"]', (element) => element.innerText))
      .toLowerCase()
      .includes("recommended limit"),
  );


  await page.screenshot({ path: path.join(ARTIFACTS, "banker-dashboard.png"), fullPage: false });

  // Dismiss decision pop-out modal before signing out
  const modalClose = await page.$('button[aria-label="Close modal"]');
  if (modalClose) {
    await domClick(page, 'button[aria-label="Close modal"]');
    await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), { timeout: 5000 });
  }

  // ------------------------------------------------------------- borrower

  await clickButtonText(page, "Sign out");
  await page.waitForFunction(() => window.location.pathname === "/", { timeout: 15000 });
  await page.waitForSelector('[data-testid="demo-farmer"]', { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 1000));
  await page.click('[data-testid="demo-farmer"]');
  await page.waitForFunction(
    () => (document.querySelector('input[type="email"]')?.value?.length ?? 0) > 0,
    { timeout: 5000 },
  );
  await page.click('[data-testid="auth-submit"]');
  await page.waitForFunction(() => window.location.pathname === "/borrower", {
    timeout: 30000,
  });
  await waitText(page, "Your credit application");
  check("Borrower login reaches the application portal", true);

  await waitText(page, "Fill demo values");
  await clickButtonText(page, "Fill demo values");
  await clickButtonText(page, "Continue");
  await clickButtonText(page, "Continue");
  await clickButtonText(page, "Continue");

  await page.waitForSelector('[data-testid="doc-upload"]', { timeout: 15000 });
  const fileInput = await page.$('[data-testid="doc-upload"]');
  await fileInput.uploadFile(UPLOAD);
  await waitText(page, "agritrust_land_record.pdf");
  check("Borrower can attach a proof document", true);

  await clickButtonText(page, "Continue");
  await page.waitForSelector('[data-testid="declaration-confirm"]');
  await domClick(page, '[data-testid="declaration-confirm"]');
  // Wait for React to commit the checkbox state before submitting; otherwise
  // the submit handler still closes over `accepted === false`.
  await page.waitForFunction(
    () => document.querySelector('[data-testid="declaration-confirm"]')?.checked === true,
    { timeout: 5000 },
  );
  await domClick(page, '[data-testid="submit-application"]');
  await waitText(page, "Application submitted", 30000);
  check("Borrower submission reaches Pending verification", true);
  check(
    "Borrower response does not display a score",
    !(await page.evaluate(() => document.body.innerText)).includes("Recommended limit"),
  );

  await page.screenshot({ path: path.join(ARTIFACTS, "borrower-portal.png"), fullPage: false });
} finally {
  await browser.close();
}

const failed = checks.filter((item) => !item.passed);
console.log(String.fromCharCode(10) + (checks.length - failed.length) + "/" + checks.length + " checks passed");
if (consoleErrors.length) {
  console.log(String.fromCharCode(10) + "Browser console errors:");
  for (const error of consoleErrors) console.log(" - " + error);
}
process.exit(failed.length || consoleErrors.length ? 1 : 0);
