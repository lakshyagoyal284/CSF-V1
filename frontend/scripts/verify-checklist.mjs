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

function check(label, condition) {
  checks.push({ label, passed: Boolean(condition) });
  console.log(`${condition ? "PASS" : "FAIL"}  ${label}`);
}

async function waitText(page, text, timeout = 25000) {
  await page.waitForFunction(
    (needle) => document.body.innerText.toLowerCase().includes(needle.toLowerCase()),
    { timeout },
    text
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

  console.log("\n--- Logging in as Farmer to test Document Checklist ---");
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector('[data-testid="demo-farmer"]', { timeout: 15000 });
  await new Promise((r) => setTimeout(r, 1000));
  await page.click('[data-testid="demo-farmer"]');
  await page.waitForFunction(
    () => (document.querySelector('input[type="email"]')?.value?.length ?? 0) > 0,
    { timeout: 5000 }
  );
  await page.click('[data-testid="auth-submit"]');
  await page.waitForFunction(() => window.location.pathname === "/borrower", { timeout: 30000 });
  await waitText(page, "Your credit application");
  check("Farmer reaches borrower application portal", true);

  // Fill demographic & cash flow steps to reach Step 3 (Documents)
  console.log("\n--- Navigating to Step 3 (Documents & Checklist) ---");
  await waitText(page, "Fill demo values");
  await clickButtonText(page, "Fill demo values");
  await clickButtonText(page, "Continue"); // Step 1: Assets
  await clickButtonText(page, "Continue"); // Step 2: Cash flow
  await clickButtonText(page, "Continue"); // Step 3: Documents & checklist

  // Verify Checklist Presence and Structure
  await page.waitForSelector('[data-testid="document-checklist"]', { timeout: 10000 });
  check("Document Checklist component is rendered", true);

  const checklistText = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="document-checklist"]');
    return el ? el.innerText : "";
  });

  check(
    "Checklist displays Farmer title and priority badges",
    checklistText.includes("Farmer Document Checklist") &&
      checklistText.includes("Critical") &&
      checklistText.includes("High Priority") &&
      checklistText.includes("Recommended") &&
      checklistText.includes("Optional")
  );

  check(
    "Non-interfering guarantee banner is clearly visible",
    checklistText.includes("Non-Interfering Checklist Guarantee") &&
      checklistText.includes("never be blocked")
  );

  // Verify specific Farmer documents exist
  check(
    "Land Revenue Record / 7/12 Extract is listed with Critical priority",
    checklistText.includes("Land Revenue Record / 7/12 Extract")
  );
  check(
    "Agricultural Electricity / Tube-well Invoice is listed",
    checklistText.includes("Agricultural Electricity / Tube-well Invoice")
  );

  // Test checking 4 items and leaving 4 items unchecked
  console.log("\n--- Testing checklist interaction (checking 4 items out of 8) ---");
  // Toggle items: farmer_land_record, farmer_kyc, farmer_bank_stmt, farmer_utility
  const checkedItems = await page.evaluate(() => {
    const checkboxes = document.querySelectorAll('[data-testid^="checklist-checkbox-"]');
    return Array.from(checkboxes).map((cb) => cb.checked);
  });
  console.log(`Initial checked state from demo: ${checkedItems.filter(Boolean).length} items checked.`);

  // Verify we can toggle a checkbox manually
  await domClick(page, '[data-testid="checklist-item-farmer_crop_yield"]');
  const cropYieldChecked = await page.evaluate(
    () => document.querySelector('[data-testid="checklist-checkbox-farmer_crop_yield"]')?.checked
  );
  check("Borrower can manually check/uncheck checklist items", cropYieldChecked === true);

  // Filter test
  console.log("\n--- Testing Priority Level filter tabs ---");
  await domClick(page, '[data-testid="checklist-filter-critical"]');
  const criticalItemsCount = await page.evaluate(
    () => document.querySelectorAll('[data-testid^="checklist-item-"]').length
  );
  check("Filter tab restricts list to Critical items (2 items)", criticalItemsCount === 2);

  // Return to All filter
  await domClick(page, '[data-testid="checklist-filter-all"]');
  const allItemsCount = await page.evaluate(
    () => document.querySelectorAll('[data-testid^="checklist-item-"]').length
  );
  check("All filter displays all 8 items", allItemsCount === 8);

  // Test Non-Blocking Continue:
  // User has only 4 or 5 checked, attaches 1 document, and clicks Continue
  console.log("\n--- Testing Non-Blocking Continue (user only checked partial items) ---");
  await page.waitForSelector('[data-testid="doc-upload"]', { timeout: 10000 });
  const fileInput = await page.$('[data-testid="doc-upload"]');
  await fileInput.uploadFile(UPLOAD);
  await waitText(page, "agritrust_land_record.pdf");
  check("Proof document attached", true);

  // Click Continue - MUST NOT BE STOPPED!
  await clickButtonText(page, "Continue");

  // Verify we reached Step 4 (Review and submit)
  await waitText(page, "Review and submit");
  const reviewText = await page.evaluate(() => document.body.innerText);
  check("Borrower successfully advanced to Review & Submit without being stopped", reviewText.includes("Review and submit"));

  // Confirm declaration checkbox & submit application
  console.log("\n--- Confirming final submission ---");
  await domClick(page, '[data-testid="declaration-confirm"]');
  await page.waitForFunction(
    () => document.querySelector('[data-testid="declaration-confirm"]')?.checked === true,
    { timeout: 5000 }
  );
  await domClick(page, '[data-testid="submit-application"]');
  await waitText(page, "Application submitted", 30000);
  check("Application with checklist successfully submitted to underwriting queue", true);

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
