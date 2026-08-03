/**
 * The state indicator is derived, never guessed.
 *
 * Each case below pins one input to one output, so a future "helpful" default
 * cannot quietly turn absence of information into a confident claim.
 */

import { describe, expect, it } from "vitest";
import { PRESENTATION, inQuietHours, runtimeState } from "./runtimeState";
import type { RuntimeInputs, RuntimeState } from "./runtimeState";

const base: RuntimeInputs = {
  link: { state: "connected" },
  agent: "idle",
  sending: false,
  pendingApprovals: 0,
  providers: [],
  quietHours: null,
};

const state = (over: Partial<RuntimeInputs> = {}) => runtimeState({ ...base, ...over });

const provider = (status: string) =>
  ({
    name: "gemini",
    available: false,
    models: [],
    status,
    reason: "",
    detail: "",
    checked_at: 0,
    latency_ms: 0,
    retry_after: 0,
    failures: 0,
  }) as never;

describe("the connection comes first", () => {
  it("is starting while the shell is still launching the engine", () => {
    expect(state({ link: { state: "starting" } })).toBe("starting");
    expect(state({ link: { state: "handshake" } })).toBe("starting");
  });

  it("is offline when there is no engine, whatever the engine last said", () => {
    // Nothing the engine reported earlier is true once it is gone.
    expect(state({ link: { state: "engine-failed", detail: "" }, agent: "working" })).toBe(
      "offline",
    );
  });

  it("keeps showing work while the socket is coming back", () => {
    expect(state({ link: { state: "retrying", attempt: 1 }, agent: "working" })).toBe(
      "executing",
    );
  });
});

describe("what the engine said", () => {
  it("maps each agent state to something a person can read", () => {
    expect(state({ agent: "working" })).toBe("executing");
    expect(state({ agent: "verifying" })).toBe("verifying");
    expect(state({ agent: "done" })).toBe("completed");
    expect(state({ agent: "failed" })).toBe("error");
  });

  it("separates waiting for an answer from waiting for a decision", () => {
    expect(state({ agent: "blocked" })).toBe("waiting_input");
    expect(state({ agent: "blocked", pendingApprovals: 1 })).toBe("waiting_approval");
  });

  it("distinguishes answering from planning by whether a request is open", () => {
    expect(state({ agent: "thinking", sending: true })).toBe("answering");
    expect(state({ agent: "thinking", sending: false })).toBe("planning");
  });
});

describe("idle is not always fine", () => {
  it("warns when a provider is broken in a way the user could fix", () => {
    expect(state({ providers: [provider("invalid_key")] })).toBe("warning");
    expect(state({ providers: [provider("rate_limit")] })).toBe("warning");
    expect(state({ providers: [provider("offline")] })).toBe("warning");
  });

  it("does not call a fresh install broken", () => {
    // Every new machine has an unconfigured provider. That is not a warning.
    expect(state({ providers: [provider("invalid_config")] })).toBe("idle");
    expect(state({ providers: [provider("unknown")] })).toBe("idle");
  });

  it("says it is being quiet when the quiet hours actually apply", () => {
    const quietHours = { enabled: true, start: "23:00", end: "08:00" };
    expect(state({ quietHours, now: new Date("2026-08-02T02:00:00") })).toBe("quiet");
    expect(state({ quietHours, now: new Date("2026-08-02T14:00:00") })).toBe("idle");
  });
});

describe("quiet hours", () => {
  it("handles a window that crosses midnight, like the default one", () => {
    const quiet = { enabled: true, start: "23:00", end: "08:00" };
    expect(inQuietHours(quiet, new Date("2026-08-02T23:30:00"))).toBe(true);
    expect(inQuietHours(quiet, new Date("2026-08-02T07:59:00"))).toBe(true);
    expect(inQuietHours(quiet, new Date("2026-08-02T08:00:00"))).toBe(false);
  });

  it("is off when it is off", () => {
    expect(inQuietHours({ enabled: false, start: "00:00", end: "23:59" })).toBe(false);
    expect(inQuietHours(null)).toBe(false);
  });
});

describe("how each state is shown", () => {
  const all = Object.keys(PRESENTATION) as RuntimeState[];

  it("gives every state a word and a mark, not only a colour", () => {
    for (const value of all) {
      expect(PRESENTATION[value].label, value).toBeTruthy();
      expect(PRESENTATION[value].mark, value).toBeTruthy();
    }
  });

  it("animates only while something is genuinely happening", () => {
    // An idle agent that pulses forever is a GPU bill and a distraction.
    expect(PRESENTATION.idle.busy).toBe(false);
    expect(PRESENTATION.completed.busy).toBe(false);
    expect(PRESENTATION.waiting_approval.busy).toBe(false);
    expect(PRESENTATION.executing.busy).toBe(true);
  });

  it("has no state the engine cannot report", () => {
    // `recovering` and `gaming` are in the brief and absent here on purpose:
    // AgentState has no recovering, and nothing detects a running game.
    expect(all).not.toContain("recovering");
    expect(all).not.toContain("gaming");
  });
});
