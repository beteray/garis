/**
 * Navigation adapts without losing anything, and works from the keyboard.
 *
 * The failures guarded: a destination disappearing when the window narrows (so
 * a feature needs a resize to find), an icon-only rail with no accessible name,
 * and an overlay a keyboard user can enter but not leave.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Nav } from "./Nav";
import { DESTINATIONS, navShape, routesFor } from "../lib/nav";

const shown = () => routesFor(false);

describe("which routes exist", () => {
  it("keeps the machinery out of the primary navigation", () => {
    expect(shown().map((d) => d.id)).toEqual([
      "home",
      "tasks",
      "memory",
      "connections",
      "settings",
    ]);
  });

  it("offers diagnostics only in developer mode", () => {
    expect(routesFor(true).map((d) => d.id)).toContain("developer");
  });

  it("has no automations route, because nothing schedules anything", () => {
    // core/src/garis has no scheduler, no source registry and no producer for
    // `subscription.item`. A route that cannot do its own name is worse than
    // no route.
    expect(DESTINATIONS.map((d) => d.id)).not.toContain("automations");
  });
});

describe("which shape", () => {
  it("follows the window when the user has not chosen", () => {
    expect(navShape("auto", 1440)).toBe("labels");
    expect(navShape("auto", 1024)).toBe("rail");
    expect(navShape("auto", 760)).toBe("overlay");
  });

  it("honours an explicit choice", () => {
    expect(navShape("rail", 1920)).toBe("rail");
    expect(navShape("labels", 1024)).toBe("labels");
  });

  it("overrides the choice only when the window cannot hold it", () => {
    // Ignoring the preference briefly beats a navigation nobody can reach.
    expect(navShape("labels", 700)).toBe("overlay");
  });

  it("treats 1024 as an ordinary desktop, not a phone", () => {
    // A 1536-pixel laptop at 150% Windows scaling reports exactly this.
    expect(navShape("auto", 1024)).not.toBe("overlay");
  });
});

describe("drawing it", () => {
  it("shows every destination in all three shapes", () => {
    for (const shape of ["labels", "rail"] as const) {
      const { unmount } = render(
        <Nav shape={shape} destinations={shown()} current="home" onNavigate={() => {}} />,
      );
      for (const destination of shown()) {
        expect(screen.getByRole("button", { name: destination.label })).toBeInTheDocument();
      }
      unmount();
    }
  });

  it("keeps the name when the label is hidden", () => {
    render(<Nav shape="rail" destinations={shown()} current="home" onNavigate={() => {}} />);
    const tasks = screen.getByRole("button", { name: "Zadania" });
    expect(tasks).toHaveAttribute("title", expect.stringContaining("Co robię"));
    expect(tasks.querySelector(".nav-item__label")).toBeNull();
  });

  it("marks the current destination for assistive technology", () => {
    render(<Nav shape="labels" destinations={shown()} current="tasks" onNavigate={() => {}} />);
    expect(screen.getByRole("button", { name: "Zadania" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("says how many things are waiting rather than showing a bare number", () => {
    render(
      <Nav
        shape="labels"
        destinations={shown()}
        current="home"
        onNavigate={() => {}}
        badges={{ tasks: 2 }}
      />,
    );
    expect(screen.getByLabelText("2 do obejrzenia")).toBeInTheDocument();
  });

  it("navigates from the keyboard", async () => {
    const onNavigate = vi.fn();
    render(<Nav shape="labels" destinations={shown()} current="home" onNavigate={onNavigate} />);

    screen.getByRole("button", { name: "Pamięć" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onNavigate).toHaveBeenCalledWith("memory");
  });
});

describe("the overlay", () => {
  const open = (onClose = () => {}) =>
    render(
      <Nav
        shape="overlay"
        destinations={shown()}
        current="home"
        onNavigate={() => {}}
        open
        onClose={onClose}
      />,
    );

  it("is a dialog, so a screen reader knows it took over", () => {
    open();
    expect(screen.getByRole("dialog", { name: "Nawigacja" })).toHaveAttribute(
      "aria-modal",
      "true",
    );
  });

  it("moves focus into itself", async () => {
    open();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Start" })).toHaveFocus(),
    );
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    open(onClose);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("closes after choosing, so the content is not left covered", async () => {
    const onClose = vi.fn();
    open(onClose);
    await userEvent.click(screen.getByRole("button", { name: "Ustawienia" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps Tab inside itself", async () => {
    open();
    const buttons = screen.getAllByRole("button");
    buttons[buttons.length - 1].focus();
    await userEvent.tab();
    // Wrapped back to the top rather than escaping to the page behind.
    expect(document.activeElement).toBe(buttons[0]);
  });
});
