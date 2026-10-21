import puppeteer from "puppeteer-core";
const step = (m) => console.log(`${new Date().toISOString()} ${m}`);

async function probe(label, url, extraArgs) {
  const browser = await puppeteer.launch({
    executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
    headless: "new",
    args: ["--no-sandbox", "--disable-gpu", ...extraArgs],
    protocolTimeout: 30000,
  });
  const page = await browser.newPage();
  page.on("response", (r) => { if (r.status() >= 400) step(`HTTP ${r.status()} ${r.url()}`); });
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 12000 });
    const t = await page.evaluate(() => document.body.innerText.slice(0, 120));
    step(`${label}: OK -> ${JSON.stringify(t)}`);
  } catch (e) {
    step(`${label}: FAILED (${e.message.split("\n")[0]})`);
  }
  await browser.close().catch(() => {});
}

await probe("D 127.0.0.1:3000 default proxy", "http://127.0.0.1:3000/", []);
await probe("E 127.0.0.1:3000 --no-proxy-server", "http://127.0.0.1:3000/", ["--no-proxy-server"]);
step("done");
