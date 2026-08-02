/**
 * The look the user asked for is the look they get.
 *
 * Two failures this guards. First: a setting that is saved, reported as saved,
 * and then ignored — the worst kind, because nothing tells you. Second: the
 * accessibility rule going the wrong way round, so someone who asked their OS
 * for less movement gets an animated welcome anyway.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_APPEARANCE,
  accentTriple,
  applyAppearance,
  hexToHsl,
} from "./appearance";

/** Pretend to be a machine with these preferences. */
function machine({ light = false, still = false } = {}) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("light") ? light : query.includes("reduced-motion") ? still : false,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

function root(): HTMLElement {
  return document.documentElement;
}

afterEach(() => vi.unstubAllGlobals());

describe("theme", () => {
  it("follows the desktop when the user has not chosen", () => {
    machine({ light: true });
    applyAppearance({ theme: "system" });
    expect(root().dataset.theme).toBe("light");

    machine({ light: false });
    applyAppearance({ theme: "system" });
    expect(root().dataset.theme).toBe("dark");
  });

  it("lets an explicit choice beat the desktop", () => {
    // The thing a `prefers-color-scheme` media query could never do.
    machine({ light: false });
    applyAppearance({ theme: "light" });
    expect(root().dataset.theme).toBe("light");
  });
});

describe("movement", () => {
  it("respects the system accessibility setting by default", () => {
    machine({ still: true });
    applyAppearance({ animation: "system" });
    expect(root().dataset.motion).toBe("off");
  });

  it("animates when nobody asked for stillness", () => {
    machine({ still: false });
    applyAppearance({ animation: "system" });
    expect(root().dataset.motion).toBe("full");
  });

  it("lets someone turn animation off inside GARIS alone", () => {
    machine({ still: false });
    applyAppearance({ animation: "off" });
    expect(root().dataset.motion).toBe("off");
  });

  it("lets someone who wants motion have it despite the OS", () => {
    machine({ still: true });
    applyAppearance({ animation: "full" });
    expect(root().dataset.motion).toBe("full");
  });
});

describe("accent", () => {
  it("accepts every preset name", () => {
    expect(accentTriple("violet")).toBe("268 85% 68%");
  });

  it("accepts a colour the user picked themselves", () => {
    expect(hexToHsl("#ff0000")).toBe("0 100% 50%");
    expect(hexToHsl("#000000")).toBe("0 0% 0%");
    expect(hexToHsl("#3366cc")).toBe("220 60% 50%");
  });

  it("never leaves the window colourless when the value is nonsense", () => {
    expect(accentTriple("różowawy")).toBe(accentTriple("cyan"));
    expect(hexToHsl("nie-kolor")).toBeNull();
  });
});

describe("glass", () => {
  it("turns the filters off entirely at zero rather than blurring by nothing", () => {
    machine();
    applyAppearance({ glass: 0 });
    expect(root().dataset.glass).toBe("off");
    expect(root().style.getPropertyValue("--glass-strength")).toBe("0");
  });

  it("clamps a value from a config edited by hand", () => {
    machine();
    applyAppearance({ glass: 9, text_scale: 99 });
    expect(root().style.getPropertyValue("--glass-strength")).toBe("1");
    expect(root().style.getPropertyValue("--text-scale")).toBe("1.4");
  });
});

describe("the whole default look", () => {
  it("puts every knob on the document", () => {
    machine();
    applyAppearance(DEFAULT_APPEARANCE);
    const { dataset } = root();
    expect(dataset.theme).toBe("dark");
    expect(dataset.density).toBe("comfortable");
    expect(dataset.contrast).toBe("normal");
    expect(dataset.navigation).toBe("labels");
    expect(root().style.getPropertyValue("--accent-hsl")).toBe(accentTriple("cyan"));
  });

  it("survives an engine that sent an older, shorter appearance block", () => {
    // A 0.1.1 engine talking to a 0.1.2 window: missing keys take defaults
    // rather than becoming "undefined" on the document.
    machine();
    applyAppearance({ theme: "dark" });
    expect(root().dataset.density).toBe("comfortable");
    expect(root().dataset.motion).toBe("full");
  });
});
