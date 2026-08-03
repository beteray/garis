/**
 * The fixtures are a photographer's backdrop. These tests keep them out of the
 * building the user actually lives in.
 */

import { afterEach, describe, expect, it } from "vitest";
import { SCENARIOS, enabled } from "./scenarios";
import { active, mounted } from "./fixture";

const url = (search: string) => {
  window.history.replaceState({}, "", `/${search}`);
};

afterEach(() => url(""));

describe("a fixture cannot reach a real build", () => {
  it("is off unless the build was made with the flag", () => {
    // The suite runs without VITE_GARIS_FIXTURES, exactly as `npm run build`
    // does. That is the shipped configuration.
    expect(enabled()).toBe(false);
  });

  it("ignores the query parameter when it is off", () => {
    url("?fixture=idle");
    expect(active()).toBeNull();
    expect(mounted()).toBe(false);
  });

  it("names the flag and the production check in one expression", () => {
    // If someone drops `!import.meta.env.PROD`, a stray env var in a release
    // pipeline would be enough to ship hand-written state as real state.
    expect(enabled.toString()).toContain("PROD");
    expect(enabled.toString()).toContain("VITE_GARIS_FIXTURES");
  });
});

describe("what a fixture is allowed to contain", () => {
  it("carries no secret value and no vault path", () => {
    const dump = JSON.stringify(SCENARIOS);
    expect(dump).not.toContain("vault://");
    // Key shapes, not prefixes: "task-running" contains "sk-", and a test that
    // cries wolf at a scenario name is a test nobody keeps.
    expect(dump).not.toMatch(/sk-[A-Za-z0-9_-]{20,}/);
    expect(dump).not.toMatch(/AIza[A-Za-z0-9_-]{20,}/);
  });

  it("says of every scenario what a picture of it would be", () => {
    for (const [name, scenario] of Object.entries(SCENARIOS)) {
      expect(scenario.note, name).toBeTruthy();
    }
  });

  it("has no scenario for a state the engine cannot produce", () => {
    // Same rule as the orb: no recovering, no gaming mode, no voice amplitude.
    const names = Object.keys(SCENARIOS);
    expect(names).not.toContain("recovering");
    expect(names).not.toContain("gaming");
    expect(JSON.stringify(SCENARIOS)).not.toContain('"level"');
  });
});
