/**
 * Screenshots of every visual state, with no engine and no network.
 *
 * Runs the Vite **dev server** with the fixture flag, then photographs each
 * scenario across the viewports and looks the brief asks for.
 *
 * Why the dev server and not a build: `import.meta.env.PROD` is true in every
 * `vite build`, whatever `--mode` says, because Vite pins NODE_ENV for builds.
 * The fixture gate reads that flag, so fixtures are *structurally* impossible in
 * anything that could be shipped — including the artefact this script would
 * otherwise have produced. Photographing the dev server is the honest way round:
 * it keeps the gate absolute instead of loosening it for the camera.
 *
 * What this proves: that the window renders these states correctly at these
 * sizes. What it does not prove: anything at all about the Python engine, a
 * provider, or a task. Every pixel comes from `src/dev/scenarios.ts`.
 *
 * Nothing may leave the machine — see the route filter below. A fixture that
 * quietly started calling a provider would fail here rather than in front of a
 * user.
 *
 *   node scripts/shots.mjs            # everything
 *   node scripts/shots.mjs idle       # one scenario
 *
 * `GARIS_CHROMIUM` points at an existing browser, for machines that already
 * have one and should not download 150 MB to take a picture.
 */

import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { readFile, readdir, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const here = resolve(fileURLToPath(new URL("..", import.meta.url)));
const out = join(here, "screenshots");
/**
 * A port nobody else is on. A fixed port would happily photograph whatever was
 * already listening — which is how an early run of this script produced
 * pictures of a stale server left over from the run before it.
 */
async function freePort() {
  const probe = createServer();
  await new Promise((ok) => probe.listen(0, "127.0.0.1", ok));
  const { port } = probe.address();
  await new Promise((ok) => probe.close(ok));
  return port;
}

/** Sizes: the smallest supported window, then the common desktop steps. */
const VIEWPORTS = [
  { name: "1024x700", width: 1024, height: 700 },
  { name: "1280x800", width: 1280, height: 800 },
  { name: "1440x900", width: 1440, height: 900 },
  { name: "1920x1080", width: 1920, height: 1080 },
  { name: "3440x1440", width: 3440, height: 1440 },
];

/** Looks. Each is a real setting a person can be in, not a theme sampler. */
const LOOKS = [
  { name: "dark", theme: "dark" },
  { name: "light", theme: "light" },
  { name: "contrast", theme: "dark", contrast: "high" },
  { name: "still", theme: "dark", motion: "off" },
];

/**
 * The full matrix is 14 × 5 × 4 = 280 files, which nobody looks at. Every
 * scenario is captured at the size most of them will be seen at; sizes and
 * looks are swept on one scenario that contains a bit of everything.
 */
const SWEEP = "task-running";
const BASE = { viewport: "1440x900", look: "dark" };

/** Start the dev server and wait for it to answer, rather than guessing. */
async function devServer(port, origin) {
  // The binary directly, not `npx`: killing the wrapper leaves the real server
  // orphaned, holding the port and this process's stdout open forever.
  const child = spawn(
    join(here, "node_modules/.bin/vite"),
    ["--port", String(port), "--strictPort", "--host", "127.0.0.1"],
    // The flag is passed here and nowhere else: there is no committed env file,
    // so no build that this script did not start can pick it up.
    {
      cwd: here,
      stdio: ["ignore", "pipe", "inherit"],
      env: { ...process.env, VITE_GARIS_FIXTURES: "1" },
    },
  );
  child.stdout.resume();
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const response = await fetch(origin);
      if (response.ok) return child;
    } catch {
      /* not up yet */
    }
    await new Promise((ok) => setTimeout(ok, 250));
  }
  child.kill();
  throw new Error("serwer deweloperski nie wystartował");
}

async function main() {
  const only = process.argv.slice(2);

  // The scenario table is the source of truth for what gets photographed, so a
  // new state cannot be added without a picture of it. Only the table itself:
  // the `EngineState` helper above it has top-level keys of its own, and a
  // photograph of "notifications" would be a photograph of nothing.
  const source = await readFile(join(here, "src/dev/scenarios.ts"), "utf8");
  const table = source.slice(source.indexOf("export const SCENARIOS"));
  const names = [...table.matchAll(/^ {2}"?([a-z][a-z-]*)"?: \{$/gm)].map((match) => match[1]);
  const scenarios = only.length ? names.filter((name) => only.includes(name)) : names;
  if (!scenarios.length) throw new Error(`brak scenariuszy: ${only.join(", ")}`);

  // Only what is about to be retaken. A run for one scenario used to empty the
  // folder, so checking one screen threw away every other picture.
  await mkdir(out, { recursive: true });
  for (const name of scenarios) {
    for (const file of await readdir(out)) {
      if (file.startsWith(`${name}--`)) await rm(join(out, file));
    }
  }

  const port = await freePort();
  const ORIGIN = `http://127.0.0.1:${port}`;
  const server = await devServer(port, ORIGIN);
  const browser = await chromium.launch({ executablePath: process.env.GARIS_CHROMIUM || undefined });
  let count = 0;

  const shoot = async (scenario, viewport, look) => {
    const page = await browser.newPage({
      viewport: { width: viewport.width, height: viewport.height },
      colorScheme: look.theme,
      reducedMotion: look.motion === "off" ? "reduce" : "no-preference",
      deviceScaleFactor: 1,
    });
    // Nothing leaves this machine. If a fixture ever tries, the picture fails.
    await page.route("**/*", (route) =>
      route.request().url().startsWith(ORIGIN) ? route.continue() : route.abort(),
    );
    await page.goto(`${ORIGIN}/?fixture=${scenario}`, { waitUntil: "networkidle" });
    await page.evaluate(
      ([theme, contrast, motion]) => {
        const root = document.documentElement;
        root.dataset.theme = theme;
        if (contrast) root.dataset.contrast = contrast;
        if (motion) root.dataset.motion = motion;
      },
      [look.theme, look.contrast ?? "", look.motion ?? ""],
    );
    // Long enough for the entry animations to settle. The orb never settles, by
    // design, so its phase differs between runs — that is not a regression.
    await page.waitForTimeout(1400);
    const file = join(out, `${scenario}--${viewport.name}--${look.name}.png`);
    await page.screenshot({ path: file });
    await page.close();
    count += 1;
    console.log(`  ${file.slice(here.length + 1)}`);
  };

  const base = VIEWPORTS.find((viewport) => viewport.name === BASE.viewport);
  const baseLook = LOOKS.find((look) => look.name === BASE.look);

  try {
    for (const scenario of scenarios) await shoot(scenario, base, baseLook);
    if (scenarios.includes(SWEEP)) {
      for (const viewport of VIEWPORTS) {
        if (viewport.name !== base.name) await shoot(SWEEP, viewport, baseLook);
      }
      for (const look of LOOKS) {
        if (look.name !== baseLook.name) await shoot(SWEEP, base, look);
      }
    }
  } finally {
    await browser.close();
    server.kill();
  }

  console.log(`\n${count} zrzutów w screenshots/ — to obrazy interfejsu, nie dowód pracy silnika.`);
}

await main();
