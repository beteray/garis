// Drive the real window against the real engine, and photograph what happens.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = resolve(fileURLToPath(new URL("..", import.meta.url)));
const [base, token] = [process.env.GARIS_BASE, process.env.GARIS_TOKEN];
const probe = createServer(); await new Promise(r => probe.listen(0, "127.0.0.1", r));
const port = probe.address().port; await new Promise(r => probe.close(r));
const vite = spawn(join(here, "node_modules/.bin/vite"), ["--port", String(port), "--strictPort", "--host", "127.0.0.1"], { cwd: here, stdio: ["ignore", "pipe", "inherit"] });
vite.stdout.resume();
const origin = `http://127.0.0.1:${port}`;
for (let i = 0; i < 80; i++) { try { if ((await fetch(origin)).ok) break; } catch {} await new Promise(r => setTimeout(r, 250)); }

const b = await chromium.launch({ executablePath: process.env.GARIS_CHROMIUM });
const p = await b.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
p.on("console", m => { if (m.type() === "error") console.log("CONSOLE ERROR:", m.text().slice(0, 200)); });
p.on("pageerror", e => console.log("PAGE ERROR:", e.message.slice(0, 200)));
await p.goto(`${origin}/?base=${encodeURIComponent(base)}&token=${encodeURIComponent(token)}`, { waitUntil: "networkidle" });
await p.waitForTimeout(2500);
await p.screenshot({ path: join(here, "live/01-open.png") });

// A fresh install opens in onboarding. A second run against the same engine does
// not — the answers are saved — so the walkthrough is skipped when it is absent.
const onboarding = await p.getByRole("dialog", { name: "Pierwsze uruchomienie" }).count();
if (onboarding) {
await p.locator(".field").first().fill("Michał");
await p.getByRole("button", { name: "Dalej", exact: true }).click();
await p.waitForTimeout(900);
await p.screenshot({ path: join(here, "live/02-persona.png") });
await p.getByRole("button", { name: "Rzeczowo", exact: true }).click();
await p.waitForTimeout(900);
await p.screenshot({ path: join(here, "live/03-key.png") });
await p.getByRole("button", { name: "Pomiń", exact: true }).click();
await p.waitForTimeout(900);
await p.screenshot({ path: join(here, "live/04-done.png") });
await p.getByRole("button", { name: "Zaczynamy", exact: true }).click();
await p.waitForTimeout(1800);
}
await p.screenshot({ path: join(here, "live/05-home.png") });

// Say hello — the rules path, no model needed.
await p.locator(".composer__input").fill("cześć");
await p.keyboard.press("Enter");
await p.waitForTimeout(2000);
await p.screenshot({ path: join(here, "live/06-hello.png") });

await p.locator(".composer__input").fill("ile to 17 * 3");
await p.keyboard.press("Enter");
await p.waitForTimeout(2000);
await p.screenshot({ path: join(here, "live/07-arithmetic.png") });

// A real request, on an install with no provider key: what actually happens.
await p.locator(".composer__input").fill("sprawdź, ile miejsca zostało na dysku");
await p.keyboard.press("Enter");
await p.waitForTimeout(5000);
await p.screenshot({ path: join(here, "live/08-task.png") });

for (const [name, label] of [["Zadania", "09-tasks"], ["Pamięć", "10-memory"], ["Połączenia", "11-connections"], ["Ustawienia", "12-settings"]]) {
  await p.getByRole("button", { name: new RegExp(name) }).first().click();
  await p.waitForTimeout(1200);
  await p.screenshot({ path: join(here, `live/${label}.png`) });
}
await b.close(); vite.kill();
console.log("done");
