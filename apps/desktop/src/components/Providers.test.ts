/**
 * A provider state the window cannot render is a provider state the user never
 * learns about. The engine has seven; this checks the window has seven.
 */

import { describe, expect, it } from "vitest";
import type { ProviderStatus } from "../lib/api";
import { PROVIDER_LOOK } from "./Providers";

const ALL: ProviderStatus[] = [
  "online",
  "offline",
  "invalid_config",
  "invalid_key",
  "timeout",
  "rate_limit",
  "unknown",
];

describe("provider states", () => {
  it("every state the engine can report has a rendering", () => {
    // Adding a status in models/health.py without one here would show a blank
    // row — the user would see nothing rather than "klucz odrzucony".
    expect(Object.keys(PROVIDER_LOOK).sort()).toEqual([...ALL].sort());
  });

  it("no state is distinguished by colour alone", () => {
    for (const status of ALL) {
      const look = PROVIDER_LOOK[status];
      expect(look.label, status).toBeTruthy();
      expect(look.mark, status).toBeTruthy();
    }
  });

  it("gives each state its own word", () => {
    const labels = ALL.map((status) => PROVIDER_LOOK[status].label);
    expect(new Set(labels).size).toBe(labels.length);
  });
});
