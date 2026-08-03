/**
 * The motion system keeps its own promises.
 *
 * These are cheap assertions about a table of numbers, and they exist because
 * every one of them describes a defect that was actually in the code before:
 * a list that animated its own height, a `motionSafe` helper nothing called,
 * durations invented per component, and a spring with enough bounce to make a
 * task card look like a toy.
 */

import { afterEach, describe, expect, it } from "vitest";
import {
  AMBIENT,
  DURATION,
  EASE,
  SPRING,
  TWEEN,
  pressable,
  prefersReducedMotion,
  stagger,
  tactile,
  variants,
} from "./motion";
import type { MotionSet } from "./motion";

const ALL: MotionSet[] = [
  "panel",
  "row",
  "message",
  "card",
  "dialog",
  "scrim",
  "sheet",
  "attention",
];

const still = (on: boolean) => {
  document.documentElement.dataset.motion = on ? "off" : "full";
};

afterEach(() => still(false));

describe("what is animated", () => {
  it("never animates a property that forces layout", () => {
    // transform and opacity only. Animating height, width, top or left makes
    // the browser re-lay-out the page every frame — this is the rule the old
    // `listItemVariants` broke by animating height and marginBottom.
    const banned = ["height", "width", "top", "left", "right", "bottom", "margin", "padding"];
    for (const set of ALL) {
      const json = JSON.stringify(variants(set));
      for (const property of banned) {
        expect(json, `${set} animuje ${property}`).not.toContain(`"${property}"`);
      }
    }
  });

  it("gives every set an entrance, a resting state and an exit", () => {
    for (const set of ALL) {
      const shape = variants(set);
      expect(Object.keys(shape), set).toEqual(
        expect.arrayContaining(["hidden", "visible", "exit"]),
      );
    }
  });

  it("uses blur on exactly one set, because a filter is the expensive one", () => {
    const blurred = ALL.filter((set) => JSON.stringify(variants(set)).includes("blur"));
    expect(blurred).toEqual(["panel"]);
  });
});

describe("reduced motion", () => {
  it("is read from the one place that resolved it", () => {
    still(true);
    expect(prefersReducedMotion()).toBe(true);
    still(false);
    expect(prefersReducedMotion()).toBe(false);
  });

  it("strips movement from every set without removing the change", () => {
    still(true);
    for (const set of ALL) {
      const shape = variants(set);
      const json = JSON.stringify(shape);
      // Opacity survives: the user must still see that something happened.
      expect(json, set).toContain("opacity");
      // Movement does not.
      for (const moving of ['"y"', '"x"', '"scale"', "blur"]) {
        expect(json, `${set} nadal rusza ${moving}`).not.toContain(moving);
      }
    }
  });

  it("answers at render, so the setting takes effect on the next paint", () => {
    // The failure this prevents: variants captured once at module load, so
    // turning animation off did nothing until a reload.
    still(false);
    const moving = JSON.stringify(variants("card"));
    still(true);
    const stillNow = JSON.stringify(variants("card"));
    expect(moving).not.toEqual(stillNow);
  });

  it("removes hover and press feedback entirely", () => {
    still(true);
    expect(tactile()).toEqual({});
    expect(pressable()).toEqual({});
  });

  it("drops the stagger, so a list arrives at once instead of crawling", () => {
    still(true);
    expect(stagger().visible).toEqual({});
  });
});

describe("the durations", () => {
  it("orders every band from immediate to spatial", () => {
    const ordered = [
      DURATION.instant,
      DURATION.control,
      DURATION.content,
      DURATION.panel,
      DURATION.spatial,
    ];
    expect([...ordered].sort((a, b) => a - b)).toEqual(ordered);
  });

  it("keeps each band inside the range its job needs", () => {
    // From the brief. Below the floor a transition reads as a glitch; above
    // the ceiling the interface feels like it is thinking about it.
    expect(DURATION.instant).toBeGreaterThanOrEqual(0.1);
    expect(DURATION.instant).toBeLessThanOrEqual(0.16);
    expect(DURATION.control).toBeGreaterThanOrEqual(0.14);
    expect(DURATION.control).toBeLessThanOrEqual(0.2);
    expect(DURATION.content).toBeGreaterThanOrEqual(0.22);
    expect(DURATION.content).toBeLessThanOrEqual(0.34);
    expect(DURATION.panel).toBeGreaterThanOrEqual(0.32);
    expect(DURATION.panel).toBeLessThanOrEqual(0.48);
    expect(DURATION.spatial).toBeGreaterThanOrEqual(0.4);
    expect(DURATION.spatial).toBeLessThanOrEqual(0.6);
  });

  it("uses the house curve everywhere a tween is used", () => {
    for (const [name, tween] of Object.entries(TWEEN)) {
      expect(tween.ease, name).toBeDefined();
    }
    // The curve is also `--ease-glass` in tokens.css. If one moves, both must.
    expect(EASE).toEqual([0.32, 0.72, 0, 1]);
  });
});

describe("the springs", () => {
  it("never bounces", () => {
    // Damping below roughly 2*sqrt(stiffness*mass) overshoots. A desktop tool
    // that overshoots reads as a toy, so every preset stays at or above it.
    for (const [name, preset] of Object.entries(SPRING)) {
      const { stiffness, damping, mass } = preset;
      const critical = 2 * Math.sqrt(stiffness * mass);
      // Allow mild underdamping (a trace of settle), never a visible bounce.
      expect(damping / critical, `${name} podskakuje`).toBeGreaterThan(0.55);
    }
  });

  it("gets heavier as the moving thing gets bigger", () => {
    expect(SPRING.control.stiffness).toBeGreaterThan(SPRING.content.stiffness);
    expect(SPRING.content.stiffness).toBeGreaterThan(SPRING.panel.stiffness);
  });
});

describe("ambient loops", () => {
  it("keeps anything that repeats forever slower than the eye tracks", () => {
    // A loop in peripheral vision that is quick enough to follow becomes the
    // only thing a person can see. Two seconds is the floor.
    for (const [name, seconds] of Object.entries(AMBIENT)) {
      expect(seconds, name).toBeGreaterThanOrEqual(2);
    }
    expect(AMBIENT.aurora).toBeGreaterThan(20);
  });
});

describe("interaction feedback", () => {
  it("answers the pointer with transform, never with layout", () => {
    const json = JSON.stringify(tactile());
    expect(json).toContain("scale");
    expect(json).not.toContain("width");
    expect(json).not.toContain("height");
  });

  it("does not lift a control that must stay put", () => {
    expect(JSON.stringify(tactile({ lift: false }))).not.toContain('"y"');
    expect(JSON.stringify(tactile())).toContain('"y"');
  });

  it("gives a disabled control no feedback at all", () => {
    expect(tactile({ disabled: true })).toEqual({});
    expect(pressable(true)).toEqual({});
  });
});

describe("stagger", () => {
  it("stops a long list from crawling in", () => {
    const short = stagger(4).visible as { transition: { staggerChildren: number } };
    const long = stagger(40).visible as { transition: { staggerChildren: number } };
    expect(long.transition.staggerChildren).toBeLessThan(short.transition.staggerChildren);
  });
});
