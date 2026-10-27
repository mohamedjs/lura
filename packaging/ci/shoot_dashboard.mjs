// Screenshots of the running app's dashboard, for the release smoke test.
//   node shoot_dashboard.mjs <path to dashboard.url> <out dir>
import { readFileSync, mkdirSync } from "node:fs";
import { chromium } from "playwright";

const [urlFile, out] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const url = readFileSync(urlFile, "utf8").trim();
const browser = await chromium.launch();
const errors = [];

async function shot(name, scheme, width, ui) {
  const page = await browser.newPage({ viewport: { width, height: 900 }, colorScheme: scheme });
  page.on("pageerror", (e) => errors.push(`${name}: ${e}`));
  page.on("console", (m) => m.type() === "error" && errors.push(`${name}: ${m.text()}`));
  await page.goto(url);
  await page.waitForTimeout(1200);
  if (ui) {
    await page.evaluate(async (lang) => {
      await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-Lura-Token": sessionStorage.getItem("lura-token") },
        body: JSON.stringify({ patch: { ui_language: lang } }),
      });
    }, ui);
    await page.reload();
    await page.waitForTimeout(1200);
  }
  await page.screenshot({ path: `${out}/dashboard-${name}.png`, fullPage: true });
  await page.close();
}

await shot("en-light", "light", 1280, "en");
await shot("ar-dark", "dark", 1280, "ar");
await shot("en-phone", "light", 390, "en");
await browser.close();
console.log(errors.length ? `console errors:\n${errors.join("\n")}` : "dashboard: no console errors");
if (errors.length) process.exit(1);
