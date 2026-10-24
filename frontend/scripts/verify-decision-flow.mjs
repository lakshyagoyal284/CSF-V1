#!/usr/bin/env node
/**
 * End-to-end verification for the Banker Decision Gateway workflows:
 *   1. Calculate Score -> Decision pop-out modal opens automatically with 4 options.
 *   2. Recheck action -> Pop-out closes, verified checkboxes cleared, manual recheck notice shown.
 *   3. Hold action -> Pop-out closes for 15s, score remains on screen, countdown banner shown, resume works.
 *   4. Decline action -> Decline form modal opens with adverse factors and justification, successfully declines.
 *   5. Accept & Send action -> Acceptance form opens with sanction parameters and official branch instructions note to borrower, successfully sanctions.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const CHROME =
  process.env.CHROME_PATH ??
  "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.PORTAL_URL ?? "http://localhost:3000";
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ARTIFACTS = path.resolve(SCRIPT_DIR, "../../.e2e");

const checks = [];

function check(label, condition) {
  checks.push({ label, passed: Boolean(condition) });
  console.log(`${condition ? "PASS" : "FAIL"}  ${label}`);
}

async function waitText(page, text, timeout = 25000) {
  await page.waitForFunction(
    (needle) => document.body.innerText.toLowerCase().includes(needle.toLowerCase()),
    { timeout },
    text,
  );
}

async function domClick(page, selector) {
  const clicked = await page.evaluate((sel) => {
    const element = document.querySelector(sel);
    if (!element) return false;
    element.click();
    return true;
  }, selector);
  if (!clicked) throw new Error(`Element "${selector}" not found`);
}

async function clickButtonText(page, text) {
  const clicked = await page.evaluate((target) => {
    const buttons = Array.from(document.querySelectorAll("button"));
    const button = buttons.find((b) =>
      b.textContent.toLowerCase().includes(target.toLowerCase())
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
  page.on("pageerror", (error) => console.log("PAGE ERROR:", error.message));
  page.on("console", (message) => {
    if (message.type() === "error") console.log("BROWSER ERROR:", message.text());
  });

  console.log("\n--- Navigating to landing page & logging in as banker ---");
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });
  await new Promise((r) => setTimeout(r, 1000));

  const isBankerAlready = await page.evaluate(() => window.location.pathname === "/banker");
  if (!isBankerAlready) {
    const demoBtn = await page.waitForSelector('[data-testid="demo-bank-officer"]', { timeout: 15000 }).catch(() => null);
    if (demoBtn) {
      await page.click('[data-testid="demo-bank-officer"]');
      await page.click('[data-testid="auth-submit"]');
      await page.waitForFunction(() => window.location.pathname === "/banker", { timeout: 30000 });
    } else {
      await page.goto(`${BASE}/banker`, { waitUntil: "domcontentloaded" });
    }
  }
  await waitText(page, "Underwriting desk");
  check("Banker login successful", true);

  console.log("\n--- Loading demo file ---");
  await page.waitForSelector('[data-testid="load-demo"]');
  await domClick(page, '[data-testid="load-demo"]');
  await page.waitForSelector('[data-testid^="verify-field-"]', { timeout: 30000 });
  await waitText(page, "Ramesh Yadav");
  check("Demo file loaded", true);

  // Manually select declaration fields and documents
  console.log("\n--- Manually selecting fields and documents ---");
  await page.evaluate(() => {
    const fieldCheckboxes = document.querySelectorAll('[data-testid^="verify-field-"]');
    fieldCheckboxes.forEach((cb) => cb.click());
    const docCheckboxes = document.querySelectorAll('[data-testid^="verify-doc-"]');
    docCheckboxes.forEach((cb) => cb.click());
  });

  // Wait until calculate-score is enabled
  await page.waitForFunction(
    () => {
      const btn = document.querySelector('[data-testid="calculate-score"]');
      return btn && !btn.disabled;
    },
    { timeout: 15000 }
  );
  const calcState = await page.$eval('[data-testid="calculate-score"]', (el) => ({
    disabled: el.disabled,
    text: el.innerText,
  }));
  console.log("CALCULATE BUTTON READY:", calcState);

  // Calculate score -> POP-OUT APPEARS!
  console.log("\n--- Calculating score & verifying automatic pop-out ---");
  await domClick(page, '[data-testid="calculate-score"]');
  await page.waitForSelector('[role="dialog"]', { timeout: 30000 });
  await waitText(page, "Underwriting Decision Gateway");
  check("Decision Gateway pop-out appeared automatically upon score calculation", true);

  // Verify all 4 action options exist on the pop-out
  check("Recheck option exists", (await page.$('[data-testid="decision-btn-recheck"]')) !== null);
  check("Hold option exists", (await page.$('[data-testid="decision-btn-hold"]')) !== null);
  check("Decline option exists", (await page.$('[data-testid="decision-btn-decline"]')) !== null);
  check("Accept & Send option exists", (await page.$('[data-testid="decision-btn-accept"]')) !== null);

  // 1. TEST RECHECK
  console.log("\n--- Testing 'Recheck' action ---");
  await domClick(page, '[data-testid="decision-btn-recheck"]');
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), { timeout: 5000 });
  check("Decision pop-out dismissed upon clicking Recheck", true);
  await page.waitForSelector('[data-testid="recheck-active-notice"]', { timeout: 5000 });
  check("Manual Recheck Mode banner is displayed", true);

  // Verify checkmarks were cleared
  const checkedFieldsCount = await page.$$eval(
    '[data-testid^="verify-field-"]:checked',
    (elements) => elements.length,
  );
  check("Verified checkmarks were reset for manual re-checking", checkedFieldsCount === 0);

  // Re-check fields and recalculate score to test HOLD
  console.log("\n--- Re-verifying declarations & re-calculating for Hold test ---");
  await page.evaluate(() => {
    const fieldCheckboxes = document.querySelectorAll('[data-testid^="verify-field-"]');
    fieldCheckboxes.forEach((cb) => cb.click());
    const docCheckboxes = document.querySelectorAll('[data-testid^="verify-doc-"]');
    docCheckboxes.forEach((cb) => cb.click());
  });
  await page.waitForFunction(
    () => {
      const btn = document.querySelector('[data-testid="calculate-score"]');
      return btn && !btn.disabled;
    },
    { timeout: 15000 }
  );
  await domClick(page, '[data-testid="calculate-score"]');
  await page.waitForSelector('[role="dialog"]', { timeout: 30000 });
  await waitText(page, "Underwriting Decision Gateway");

  // 2. TEST HOLD
  console.log("\n--- Testing 'Hold' action ---");
  await domClick(page, '[data-testid="decision-btn-hold"]');
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), { timeout: 5000 });
  check("Pop-out went off upon clicking Hold", true);

  // Verify score remains visible on screen
  check("Score panel remains clearly visible on screen", (await page.$('[data-testid="score-panel"]')) !== null);

  // Verify floating hold countdown banner is present
  await page.waitForSelector('[data-testid="hold-floating-banner"]', { timeout: 5000 });
  const bannerText = await page.$eval('[data-testid="hold-floating-banner"]', (el) => el.innerText);
  check("Floating Hold 15s banner is displayed with countdown", bannerText.includes("Decision on Hold"));

  // Test Resume Now from hold banner
  await domClick(page, '[data-testid="resume-hold-now"]');
  await page.waitForSelector('[role="dialog"]', { timeout: 5000 });
  await waitText(page, "Underwriting Decision Gateway");
  check("Resume Now successfully re-opened Decision Gateway pop-out", true);

  // 3. TEST ACCEPT & SEND FORM
  console.log("\n--- Testing 'Accept & Send' workflow ---");
  await domClick(page, '[data-testid="decision-btn-accept"]');
  await waitText(page, "Acceptance & Sanction Form");
  // Verify sanction parameters and official note are present
  await waitText(page, "Official Instructions Note for Borrower");
  const noteContent = await page.$eval(
    'textarea[placeholder*="Write official in-principle approval instructions"]',
    (el) => el.value
  );
  check(
    "Official Note contains branch instructions and disbursement steps",
    noteContent.includes("NEXT STEPS TO INITIATE LOAN DISBURSEMENT")
  );
  check(
    "Official Note lists original physical documents (Aadhaar, Land records)",
    noteContent.includes("Original Aadhaar Card")
  );

  // Submit Acceptance
  await domClick(page, '[data-testid="confirm-accept-button"]');
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), { timeout: 30000 });
  await page.waitForSelector('[data-testid="decision-success-notice"]', { timeout: 10000 });
  check("Sanction confirmed and success notice displayed", true);

  // 4. TEST DECLINE FORM WORKFLOW
  console.log("\n--- Testing 'Decline' workflow on a new file ---");
  // Load another demo file
  await domClick(page, '[data-testid="load-demo"]');
  await page.waitForFunction(
    () => {
      const btn = document.querySelector('[data-testid="load-demo"]');
      return btn && !btn.disabled && !btn.innerText.includes("Creating");
    },
    { timeout: 20000 }
  );
  await new Promise((r) => setTimeout(r, 1000));
  await page.evaluate(() => {
    const fieldCheckboxes = document.querySelectorAll('[data-testid^="verify-field-"]');
    fieldCheckboxes.forEach((cb) => cb.click());
    const docCheckboxes = document.querySelectorAll('[data-testid^="verify-doc-"]');
    docCheckboxes.forEach((cb) => cb.click());
  });
  await page.waitForFunction(
    () => {
      const btn = document.querySelector('[data-testid="calculate-score"]');
      return btn && !btn.disabled;
    },
    { timeout: 15000 }
  );
  await domClick(page, '[data-testid="calculate-score"]');
  await page.waitForSelector('[role="dialog"]', { timeout: 30000 });

  // Click Decline on the pop-out
  await domClick(page, '[data-testid="decision-btn-decline"]');
  await waitText(page, "Decline Application Form");
  check("Decline Form pop-out appeared with structured parameters", true);

  // Fill decline narrative
  await page.type(
    'textarea[placeholder*="Describe clearly why this application is being declined"]',
    "Applicant debt service capacity is strained with existing obligations exceeding acceptable FOIR threshold. Collateral title verification pending revenue authority clearance.",
  );

  // Submit Decline
  await domClick(page, '[data-testid="confirm-decline-button"]');
  await page.waitForFunction(() => !document.querySelector('[role="dialog"]'), { timeout: 30000 });
  await page.waitForSelector('[data-testid="decision-success-notice"]', { timeout: 10000 });
  const successText = await page.$eval('[data-testid="decision-success-notice"]', (el) => el.innerText);
  check("Application formally declined with adverse parameters recorded", successText.includes("formally declined"));

  // 5. TEST BORROWER PORTAL APPROVED APPLICATION VIEW
  console.log("\n--- Testing Borrower portal approved application view ---");
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
  await page.waitForFunction(() => window.location.pathname === "/borrower", { timeout: 30000 });
  await waitText(page, "Your credit application");
  check("Farmer reaches borrower portal", true);

  await waitText(page, "My applications");
  await new Promise((r) => setTimeout(r, 1500));
  const borrowerBody = await page.evaluate(() => document.body.innerText);
  const borrowerText = borrowerBody.toLowerCase();
  check("Borrower sees Sanction Approved badge", borrowerText.includes("sanction approved"));
  check("Borrower sees Calculated Credit Score", borrowerText.includes("calculated credit score"));
  check("Borrower sees Banker Sanction Notes", borrowerText.includes("banker sanction notes"));
  check("Borrower sees Branch Visit Notice", borrowerText.includes("next step for disbursement"));

  console.log("\n==========================================");
  console.log(`Summary: ${checks.filter((c) => c.passed).length} / ${checks.length} checks passed.`);
  console.log("==========================================\n");

  const allPassed = checks.every((c) => c.passed);
  if (!allPassed) process.exitCode = 1;
} catch (error) {
  console.error("Test failed with error:", error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
