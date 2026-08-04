/**
 * How a scenario reaches the window.
 *
 * `?fixture=<name>` fills the store with a hand-written snapshot and tells the
 * shell to skip discovery — no engine is launched, no token is read, no socket
 * is opened, no provider is called. That is the whole point: the earlier
 * screenshot attempt hung because it needed a live engine, and a live engine
 * meant a real vault and a real network round trip.
 *
 * The scenarios themselves are behind a dynamic `import()`, so the table is a
 * separate chunk that a production build never references and never loads. The
 * static version put ten kilobytes of hand-written state into the shipped
 * bundle: unreachable, but present, and "unreachable" is a claim that gets
 * weaker every time someone edits the code around it.
 */

import type { Link } from "../lib/api";
import type { Appearance } from "../lib/appearance";
import { applyAppearance } from "../lib/appearance";
import { useStore } from "../lib/store";
import type { Scenario } from "./scenarios";

/**
 * The gate. Fixtures exist only when the bundle was built with the flag, and
 * `import.meta.env.PROD` can never satisfy it — Vite pins NODE_ENV=production
 * for every build, whatever `--mode` says, so this is false in anything that
 * could ship. Both halves are build-time literals, so the check folds to
 * `false` and the dynamic import below becomes unreachable.
 */
export function enabled(): boolean {
  return import.meta.env.VITE_GARIS_FIXTURES === "1" && !import.meta.env.PROD;
}

/** The scenario in force, once `mount()` has resolved. */
let current: Scenario | null = null;

/** True when the shell must not try to reach an engine. */
export const mounted = (): boolean => current !== null;

/**
 * What the connection pill should say. A fixture never proves a connection, so
 * this is a rendering instruction and nothing more.
 */
export function link(): Link {
  return current?.link ?? { state: "connected" };
}

/**
 * Fill the store before the first paint, so nothing renders twice.
 *
 * Awaited by `main.tsx`: the scenario has to be in the store before React
 * mounts, or the shell starts hunting for an engine that is not there.
 */
export async function mount(): Promise<void> {
  if (!enabled()) return;
  if (typeof window === "undefined") return;

  const { SCENARIOS } = await import("./scenarios");

  // The whole table, published to the page. The screenshot runner reads what to
  // photograph from here rather than parsing the source file — the parser it
  // replaces attributed one scenario's extra captures to the next one along.
  // Defined inside the gate, so it exists only where the fixtures do.
  const shelf = window as unknown as {
    __garisFixtures?: unknown;
    __garisLook?: (look: Partial<Appearance>) => void;
  };
  shelf.__garisFixtures = SCENARIOS;

  // Photograph a look the way a person would get it: through the resolver, not
  // by stamping attributes on <html>. Stamping missed everything the resolver
  // writes as an inline style, which is how a "high contrast" screenshot came
  // out with the glass untouched.
  shelf.__garisLook = (look) => {
    const engine = useStore.getState().engine;
    applyAppearance({ ...(engine?.appearance ?? {}), ...look });
  };

  const name = new URLSearchParams(window.location.search).get("fixture");
  if (!name) return;

  const scenario = SCENARIOS[name];
  if (!scenario) {
    console.warn(`GARIS: nie ma fikstury „${name}".`);
    return;
  }

  current = scenario;
  useStore.setState(scenario.state as never);
  // Loud enough that nobody mistakes a fixture screenshot for a real session.
  console.warn(`GARIS: fikstura wizualna „${scenario.note}". To nie jest prawdziwa sesja.`);
}
