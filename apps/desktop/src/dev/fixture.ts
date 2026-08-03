/**
 * How a scenario reaches the window.
 *
 * `?fixture=<name>` fills the store with a hand-written snapshot and tells the
 * shell to skip discovery — no engine is launched, no token is read, no socket
 * is opened, no provider is called. That is the whole point: the earlier
 * screenshot attempt hung because it needed a live engine, and a live engine
 * meant a real vault and a real network round trip.
 *
 * Every function here answers "no" when `enabled()` is false, which a
 * production build makes unconditional at compile time.
 */

import type { Link } from "../lib/api";
import { useStore } from "../lib/store";
import { SCENARIOS, enabled } from "./scenarios";
import type { Scenario } from "./scenarios";

/** The scenario asked for in the URL, if fixtures are on and the name exists. */
export function active(): Scenario | null {
  if (!enabled()) return null;
  if (typeof window === "undefined") return null;
  const name = new URLSearchParams(window.location.search).get("fixture");
  if (!name) return null;
  return SCENARIOS[name] ?? null;
}

/** True when the shell must not try to reach an engine. */
export const mounted = (): boolean => active() !== null;

/**
 * What the connection pill should say. A fixture never proves a connection, so
 * this is a rendering instruction and nothing more.
 */
export function link(): Link {
  return active()?.link ?? { state: "connected" };
}

/** Fill the store before the first paint, so nothing renders twice. */
export function mount(): void {
  const scenario = active();
  if (!scenario) return;
  useStore.setState(scenario.state as never);
  // Loud enough that nobody mistakes a fixture screenshot for a real session.
  console.warn(`GARIS: fikstura wizualna „${scenario.note}". To nie jest prawdziwa sesja.`);
}
