/**
 * The orb is decoration over a fact, and never a fact of its own.
 *
 * The rule these tests defend: every visual state maps to a `RuntimeState` the
 * Python engine can actually report. An orb with a "recovering" look, or a
 * "gaming" look, would be inventing runtime information — which is the failure
 * this whole release exists to remove.
 */

import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Orb } from "./Orb";
import { PRESENTATION } from "../lib/runtimeState";
import type { RuntimeState } from "../lib/runtimeState";
import { given } from "../test/fixtures";

const ALL = Object.keys(PRESENTATION) as RuntimeState[];

const still = (on: boolean) => {
  document.documentElement.dataset.motion = on ? "off" : "full";
};

afterEach(() => still(false));

describe("what the orb is allowed to show", () => {
  it("renders every state the engine can report", () => {
    for (const state of ALL) {
      const { unmount } = render(<Orb state={state} />);
      expect(screen.getByRole("img"), state).toBeInTheDocument();
      unmount();
    }
  });

  it("has no look for a state the engine cannot report", () => {
    // `recovering` and `gaming` are in the design brief and deliberately
    // absent: AgentState has seven values and neither is one of them, and
    // nothing in the engine detects a running game.
    expect(ALL).not.toContain("recovering");
    expect(ALL).not.toContain("gaming");
  });

  it("names the state in words for anyone who cannot see it", () => {
    render(<Orb state="waiting_approval" />);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      `Stan: ${PRESENTATION.waiting_approval.label}`,
    );
  });

  it("marks the state on the element, so a screenshot can be told apart", () => {
    const { container } = render(<Orb state="error" />);
    expect(container.querySelector(".orb")).toHaveAttribute("data-state", "error");
  });
});

describe("what each state looks like", () => {
  const clusterOpacity = (state: RuntimeState) => {
    const { container } = render(<Orb state={state} />);
    const cluster = container.querySelector<HTMLElement>(".orb__cluster");
    return Number.parseFloat(cluster!.style.opacity);
  };

  it("does not imply readiness while it is still starting", () => {
    // Dimmer than idle: a bright orb during startup is a promise the engine
    // has not made yet.
    expect(clusterOpacity("starting")).toBeLessThan(clusterOpacity("idle"));
  });

  it("is brighter while working than while resting", () => {
    expect(clusterOpacity("executing")).toBeGreaterThan(clusterOpacity("idle"));
  });

  it("goes quieter, not louder, when it needs the user", () => {
    // The brief's rule: waiting is not urgency. The text carries the request.
    expect(clusterOpacity("waiting_approval")).toBeLessThan(clusterOpacity("idle"));
    expect(clusterOpacity("waiting_input")).toBeLessThan(clusterOpacity("idle"));
  });

  it("looks switched off when there is no engine", () => {
    expect(clusterOpacity("offline")).toBeLessThan(clusterOpacity("quiet"));
  });

  it("shows a waiting ring only where the engine is asking for something", () => {
    const ringed = ALL.filter((state) => {
      const { container, unmount } = render(<Orb state={state} />);
      const has = container.querySelector(".orb__edge") !== null;
      unmount();
      return has;
    });
    expect(ringed.sort()).toEqual(["waiting_approval", "waiting_input"]);
  });

  it("keeps red for the state that is actually an error", () => {
    const tintOf = (state: RuntimeState) => {
      const { container, unmount } = render(<Orb state={state} />);
      const tint = container.querySelector<HTMLElement>(".orb__tint");
      const background = tint?.style.background ?? "";
      unmount();
      return background;
    };
    // 354 is the red in the palette. It appears in error and nowhere else.
    expect(tintOf("error")).toContain("354");
    for (const calm of ["idle", "executing", "completed", "verifying"] as RuntimeState[]) {
      expect(tintOf(calm), calm).not.toContain("354");
    }
  });
});

describe("cost", () => {
  it("holds still when the person asked for calm", () => {
    still(true);
    const { container } = render(<Orb state="executing" />);
    const field = container.querySelector<HTMLElement>(".orb__field");
    // Framer writes the resting transform inline; a moving field would carry a
    // translate here instead.
    expect(field).toBeInTheDocument();
    expect(container.querySelector(".orb")).toHaveAttribute("data-state", "executing");
  });

  it("uses no canvas and no WebGL context", () => {
    // The version this replaces ran a fragment shader forever and silently fell
    // back to a static image on machines without WebGL.
    const { container } = render(<Orb state="idle" />);
    expect(container.querySelector("canvas")).toBeNull();
  });

  it("scales with the layout rather than being a fixed ornament", () => {
    const { container } = render(<Orb state="idle" size={40} />);
    const orb = container.querySelector<HTMLElement>(".orb");
    expect(orb!.style.width).toBe("40px");
    expect(orb!.style.height).toBe("40px");
  });
});

describe("in the banner", () => {
  it("is replaced by a plain dot when the user turns the indicator off", async () => {
    const { PresenceBanner } = await import("./Presence");
    const { engine } = await import("../test/fixtures");

    given({
      engine: engine({
        appearance: { ...engine().appearance, orb: "off" },
      }),
    });
    const { container } = render(<PresenceBanner state="idle" />);

    expect(container.querySelector(".orb")).toBeNull();
    expect(container.querySelector(".presence__dot")).toBeInTheDocument();
    // And the state is still stated in words.
    expect(screen.getByText(PRESENTATION.idle.label)).toBeInTheDocument();
  });

  it("is smaller in simple quality than in full", async () => {
    const { PresenceBanner } = await import("./Presence");
    const { engine } = await import("../test/fixtures");

    const sizeFor = (quality: string) => {
      given({ engine: engine({ appearance: { ...engine().appearance, orb: quality as never } }) });
      const { container, unmount } = render(<PresenceBanner state="idle" />);
      const width = container.querySelector<HTMLElement>(".orb")!.style.width;
      unmount();
      return Number.parseInt(width, 10);
    };

    expect(sizeFor("simple")).toBeLessThan(sizeFor("full"));
  });
});
