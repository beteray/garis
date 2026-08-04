/**
 * The whole window, without a mouse.
 *
 * Every assertion here is something a person navigating by keyboard would
 * notice within a minute: a control they can reach but cannot see, a control
 * they can see but cannot reach, focus landing on nothing after a screen
 * changes, or a disabled button that still costs a Tab press.
 *
 * The focus-ring assertions read the stylesheet rather than the computed style,
 * because jsdom does not apply a linked stylesheet. That is weaker than a real
 * browser check and it is not pretending otherwise — its job is to stop someone
 * deleting the outline "because the design looks cleaner without it".
 */

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { Composer } from "./components/Composer";
import { SettingsView } from "./components/Settings";
import { TaskCard } from "./components/TaskCard";
import { useStore } from "./lib/store";
import { engine, given, task } from "./test/fixtures";
// The stylesheets as text: jsdom does not apply a linked stylesheet, so the
// rules are read rather than computed. Vite hands them over with `?raw`.
import globalCss from "./styles/global.css?raw";
import tokenCss from "./styles/tokens.css?raw";

/** Comments stripped: a rule *about* `outline: none` is not `outline: none`. */
const strip = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "");
const css = strip(globalCss);
const tokens = strip(tokenCss);

describe("focus is always visible", () => {
  it("draws a ring on anything focused by keyboard", () => {
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:/);
  });

  it("never removes that ring anywhere", () => {
    // `outline: none` on a focusable thing is the single most common way an
    // interface becomes unusable by keyboard while looking finished.
    const removals = [...css.matchAll(/outline:\s*(none|0)\b/g)];
    expect(removals).toHaveLength(0);
  });

  it("thickens it in high contrast rather than dropping it", () => {
    expect(css).toMatch(/\[data-contrast="high"\][^{]*:focus-visible\s*\{[^}]*outline-width/);
  });

  it("keeps a ring that works on both light and dark surfaces", () => {
    // It is drawn in the accent, and the accent is defined per theme in tokens.
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:[^;]*var\(--accent\)/);
    expect(tokens).toMatch(/\[data-theme="light"\]/);
  });
});

describe("stillness never costs function", () => {
  it("stops movement without stopping the interface", () => {
    // Durations collapse; nothing is set to `display: none` or made unclickable.
    expect(tokens).toMatch(/\[data-motion="off"\][^{]*\{[^}]*--dur-base:\s*1ms/);
    expect(tokens).toMatch(/prefers-reduced-motion: reduce/);
  });
});

describe("the keyboard reaches everything, and nothing it should not", () => {
  it("does not spend a Tab stop on a control that cannot be used", async () => {
    given({});
    render(<Composer />);
    const mic = screen.getByRole("button", { name: /Mówienie/ });

    // Disabled, because speech is not implemented — and a disabled control is
    // skipped by the browser, so it costs nothing to pass over.
    expect(mic).toBeDisabled();
    await userEvent.tab();
    expect(mic).not.toHaveFocus();
  });

  it("opens a task's detail from the keyboard and says it is open", async () => {
    given({ tasks: [task({ state: "running" })] });
    render(<TaskCard taskId="t1" />);
    const details = screen.getByRole("button", { name: "Szczegóły" });

    details.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(details).toHaveAttribute("aria-expanded", "true"));
  });

  it("moves through the settings categories in the order they are read", async () => {
    given({});
    render(<SettingsView />);
    const tabs = screen.getAllByRole("button").slice(0, 3);

    tabs[0].focus();
    await userEvent.tab();
    expect(tabs[1]).toHaveFocus();
    await userEvent.tab();
    expect(tabs[2]).toHaveFocus();
  });

  it("switches category with Enter, and says which one is current", async () => {
    given({});
    render(<SettingsView />);
    const look = screen.getByRole("button", { name: "Wygląd" });

    look.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(look).toHaveAttribute("aria-current", "true"));
    // And exactly one is current: two would be a lie about where you are.
    expect(screen.getAllByRole("button", { current: true })).toHaveLength(1);
  });
});

describe("a route change", () => {
  it("leaves focus on the navigation item that caused it", async () => {
    given({ engine: engine() });
    render(<App />);

    const tasks = await screen.findByRole("button", { name: /Zadania/ });
    await userEvent.click(tasks);

    // Stealing focus here would move the keyboard away from the list the person
    // is walking through, and put a ring around the whole window for a pointer
    // user who asked for none of it.
    await waitFor(() => expect(tasks).toHaveFocus());
  });

  it("catches focus when the screen it was on goes away", async () => {
    given({ engine: engine() });
    render(<App />);
    await screen.findByRole("button", { name: /Zadania/ });

    // What a control being removed by the route change looks like from here.
    (document.activeElement as HTMLElement | null)?.blur();
    useStore.getState().setView("memory");

    await waitFor(() =>
      expect(document.activeElement).toBe(document.querySelector("main.shell__stage")),
    );
  });

  it("names the screen that arrived", async () => {
    given({ engine: engine() });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /Pamięć/ }));
    await waitFor(() =>
      expect(document.querySelector("main.shell__stage")).toHaveAttribute(
        "aria-label",
        "Pamięć",
      ),
    );
  });
});
