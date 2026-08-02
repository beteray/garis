/**
 * What GARIS is doing, derived from things that are actually measured.
 *
 * Every state below is computed from a fact the window already holds: the
 * connection state, an `agent.state` event the engine emitted, whether an
 * approval is genuinely pending, whether a request is genuinely in flight, what
 * the provider health says, what the quiet-hours setting says. Nothing here is
 * an optimistic guess about what the engine is probably up to — that is how an
 * indicator ends up confidently wrong, which is worse than absent.
 *
 * Two states from the brief are missing on purpose, because the engine has no
 * way to report them and inventing them would be a lie:
 *
 *   `recovering` — `AgentState` in `core/src/garis/agent/loop.py` has seven
 *     values and none of them is recovering. Step repair happens inside
 *     `executing` and never surfaces as its own event. Needs an engine change.
 *   `gaming` — `notifications.suppress_while_gaming` exists as a *setting*, but
 *     nothing in the engine detects a running game, so the window can only ever
 *     know the preference, never the situation.
 *
 * Both are recorded in docs/STATE_MACHINES.md as a missing contract rather than
 * faked here.
 */

import type { AgentState, Link, Provider } from "./api";

export type RuntimeState =
  | "starting"
  | "offline"
  | "idle"
  | "receiving"
  | "answering"
  | "planning"
  | "executing"
  | "verifying"
  | "waiting_input"
  | "waiting_approval"
  | "completed"
  | "warning"
  | "error"
  | "quiet";

export interface Presentation {
  /** Colour, always paired with the other two — never the only signal. */
  tone: string;
  /** A shape that reads at 12px and in a screenshot. */
  mark: string;
  /** The word. What a screen reader says, and what survives animation being off. */
  label: string;
  /** Whether the indicator should be animating at all. */
  busy: boolean;
}

export const PRESENTATION: Record<RuntimeState, Presentation> = {
  starting: { tone: "var(--state-busy)", mark: "◌", label: "Uruchamiam się", busy: true },
  offline: { tone: "var(--state-error)", mark: "⚠", label: "Brak silnika", busy: false },
  idle: { tone: "var(--state-idle)", mark: "●", label: "Czekam", busy: false },
  receiving: { tone: "var(--state-listening)", mark: "◍", label: "Przyjmuję", busy: true },
  answering: { tone: "var(--state-speaking)", mark: "◍", label: "Odpowiadam", busy: true },
  planning: { tone: "var(--state-thinking)", mark: "◐", label: "Planuję", busy: true },
  executing: { tone: "var(--state-working)", mark: "◑", label: "Wykonuję", busy: true },
  verifying: { tone: "var(--state-working)", mark: "◒", label: "Sprawdzam wynik", busy: true },
  waiting_input: { tone: "var(--state-warn)", mark: "?", label: "Czekam na odpowiedź", busy: false },
  waiting_approval: { tone: "var(--state-warn)", mark: "!", label: "Czekam na zgodę", busy: false },
  completed: { tone: "var(--state-ok)", mark: "✓", label: "Gotowe", busy: false },
  warning: { tone: "var(--state-warn)", mark: "⚠", label: "Coś wymaga uwagi", busy: false },
  error: { tone: "var(--state-error)", mark: "✕", label: "Nie udało się", busy: false },
  quiet: { tone: "var(--state-idle)", mark: "☾", label: "Cisza nocna", busy: false },
};

export interface RuntimeInputs {
  link: Link;
  agent: AgentState;
  /** True only while a request to the engine is genuinely open. */
  sending: boolean;
  /** Approvals actually returned by the engine, not a local guess. */
  pendingApprovals: number;
  providers: Provider[];
  quietHours: { enabled: boolean; start: string; end: string } | null;
  /** Injected so the test can pick a time instead of waiting for one. */
  now?: Date;
}

/** Is the clock inside the quiet window? Mirrors `QuietHours.contains`. */
export function inQuietHours(
  quiet: RuntimeInputs["quietHours"],
  now: Date = new Date(),
): boolean {
  if (!quiet?.enabled) return false;
  const minutes = (value: string) => {
    const [hh, mm] = value.split(":").map(Number);
    return Number.isFinite(hh) && Number.isFinite(mm) ? hh * 60 + mm : null;
  };
  const start = minutes(quiet.start);
  const end = minutes(quiet.end);
  if (start === null || end === null || start === end) return false;
  const at = now.getHours() * 60 + now.getMinutes();
  return start < end ? at >= start && at < end : at >= start || at < end;
}

/** A provider that is broken in a way the user could fix. Being unconfigured is
 *  not a warning — it is the state of every fresh install. */
const isBroken = (provider: Provider): boolean =>
  provider.status === "invalid_key" ||
  provider.status === "rate_limit" ||
  provider.status === "offline";

export function runtimeState(inputs: RuntimeInputs): RuntimeState {
  const { link, agent, sending, pendingApprovals, providers, quietHours, now } = inputs;

  // Connection first: nothing the engine said earlier is true if it is gone.
  if (link.state === "starting" || link.state === "handshake") return "starting";
  if (link.state !== "connected" && link.state !== "retrying") return "offline";

  if (agent === "blocked") {
    return pendingApprovals > 0 ? "waiting_approval" : "waiting_input";
  }
  if (agent === "thinking") return sending ? "answering" : "planning";
  if (agent === "working") return "executing";
  if (agent === "verifying") return "verifying";
  if (agent === "failed") return "error";
  if (agent === "done") return "completed";

  // Idle, but not necessarily fine.
  if (sending) return "receiving";
  if (pendingApprovals > 0) return "waiting_approval";
  if (providers.some(isBroken)) return "warning";
  if (inQuietHours(quietHours, now)) return "quiet";
  return "idle";
}
