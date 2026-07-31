// Look at the interface the way a user does, and click the things they clicked.
import { chromium } from "playwright";

const BASE = process.env.API_BASE;
const TOKEN = process.env.API_TOKEN;
const URL = `http://127.0.0.1:4173/?base=${encodeURIComponent(BASE)}&token=${encodeURIComponent(TOKEN)}`;
const SHOTS = process.env.SHOTS || "/tmp/shots";

const browser = await chromium.launch({
  executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
});
const page = await browser.newPage({ viewport: { width: 1160, height: 760 } });

const errors = [];
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("requestfailed", (r) => errors.push(`net: ${r.url()} ${r.failure()?.errorText}`));
page.on("response", (r) => { if (r.status() >= 400) errors.push(`http ${r.status()}: ${r.url()}`); });

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForTimeout(3000);
await page.screenshot({ path: `${SHOTS}/01-first-run.png` });

console.log("=== BŁĘDY ===");
console.log(errors.length ? [...new Set(errors)].join("\n") : "(brak)");

console.log("\n=== STATUS ===");
console.log(await page.locator("text=/łączę się|v0\\.1\\.0/i").first().textContent().catch(() => "?"));

const onboarding = await page
  .locator("text=/Zaczynajmy|Jak mam się do Ciebie/i")
  .first()
  .isVisible()
  .catch(() => false);
console.log(`\n=== ONBOARDING WIDOCZNY: ${onboarding} ===`);
if (onboarding) {
  console.log((await page.locator("body").innerText()).slice(0, 300));
  for (let i = 0; i < 12; i++) {
    const next = page.locator("button", { hasText: /Dalej|Gotowe|Zaczynajmy|Pomiń|Zamknij/i }).first();
    if (!(await next.isVisible().catch(() => false))) break;
    await next.click().catch(() => {});
    await page.waitForTimeout(400);
  }
  await page.screenshot({ path: `${SHOTS}/02-after-onboarding.png` });
}

console.log("\n=== CO PRZECHWYTUJE KLIKNIĘCIA W NAWIGACJI ===");
const probe = page.locator("button", { hasText: "Ustawienia" }).first();
const box = await probe.boundingBox().catch(() => null);
if (box) {
  const info = await page.evaluate(([x, y]) => {
    const el = document.elementFromPoint(x, y);
    const path = [];
    for (let n = el; n && path.length < 4; n = n.parentElement) {
      path.push(`${n.tagName.toLowerCase()}${n.className ? "." + String(n.className).split(" ").join(".") : ""}`);
    }
    return path.join("  <  ");
  }, [box.x + box.width / 2, box.y + box.height / 2]);
  console.log(info);
}

console.log("\n=== NAWIGACJA ===");
for (const label of ["Zadania", "Pamięć", "Urządzenia", "Ustawienia", "Diagnostyka", "Start"]) {
  try {
    await page.locator("button", { hasText: label }).first().click({ timeout: 3000 });
    await page.waitForTimeout(450);
    console.log(`  ${label}: OK`);
  } catch (e) {
    console.log(`  ${label}: NIEUDANE — ${String(e.message).split("\n")[0].slice(0, 80)}`);
  }
}

console.log("\n=== PRZEPŁYW KLUCZA API ===");
await page.locator("button", { hasText: "Ustawienia" }).first().click().catch(() => {});
await page.waitForTimeout(600);
await page.screenshot({ path: `${SHOTS}/03-settings.png` });
const addButtons = page.locator("button", { hasText: "Dodaj klucz" });
console.log(`przycisków „Dodaj klucz": ${await addButtons.count()}`);
try {
  await addButtons.nth(2).click({ timeout: 3000 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${SHOTS}/04-key-dialog.png` });
  console.log(`  pól tekstowych: ${await page.locator("input").count()}`);
  console.log(`  wokół Gemini: ${JSON.stringify((await page.locator("body").innerText()).match(/Gemini[\s\S]{0,140}/)?.[0] ?? "")}`);
} catch (e) {
  console.log(`  NIEUDANE — ${String(e.message).split("\n")[0].slice(0, 90)}`);
}

console.log("\n=== RESPONSYWNOŚĆ ===");
for (const [w, h] of [[900, 620], [1160, 760], [1920, 1080], [1280, 700]]) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(500);
  const m = await page.evaluate(() => ({
    sw: document.body.scrollWidth,
    cw: document.documentElement.clientWidth,
    sh: document.body.scrollHeight,
    ch: document.documentElement.clientHeight,
  }));
  const bad = m.sw > m.cw + 1 || m.sh > m.ch + 1;
  console.log(`  ${w}x${h}: ${bad ? "PRZEPEŁNIENIE " + JSON.stringify(m) : "ok"}`);
  await page.screenshot({ path: `${SHOTS}/05-size-${w}x${h}.png` });
}

await browser.close();
