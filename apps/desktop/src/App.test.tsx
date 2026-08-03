/**
 * Every route renders, and the shell adapts without losing anything.
 *
 * These are the tests that would have caught the old screens: a route that
 * showed local `useState` as if it were an automation, and a settings screen
 * whose controls did not reach the engine.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { ConnectionsView } from "./components/Connections";
import { SettingsView } from "./components/Settings";
import { OVERLAY_BELOW, RAIL_BELOW } from "./lib/nav";
import { useStore } from "./lib/store";
import { device, engine, fakeApi, given, tool } from "./test/fixtures";

/**
 * The shell discovers and connects to an engine on mount. There is none here,
 * so transport is replaced with one that echoes back whatever `given()` put in
 * the store — the screens are what is under test, not the socket.
 */
vi.mock("./lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/api")>();
  const { useStore } = await import("./lib/store");
  return {
    ...actual,
    discover: async () => ({ base: "http://127.0.0.1:8756", token: "t", error: "", fromShell: false }),
    GarisApi: class {
      health = async () => ({ ok: true, protocol: 1 });
      state = async () => useStore.getState().engine;
      tasks = async () => ({ tasks: useStore.getState().tasks });
      approvals = async () => ({ approvals: useStore.getState().approvals });
      memories = async () => ({ memories: useStore.getState().memories, stats: {} });
      secrets = async () => ({ secrets: useStore.getState().secrets });
      devices = async () => ({ devices: useStore.getState().devices });
      tools = async () => ({ tools: useStore.getState().tools });
      audit = async () => ({ audit: [] });
      connect = () => () => {};
    },
  };
});

function mountApp(width = 1440) {
  window.innerWidth = width;
  return render(<App />);
}

describe("routes", () => {
  it("offers exactly the five everyday destinations", async () => {
    given({});
    mountApp();
    for (const label of ["Start", "Zadania", "Pamięć", "Połączenia", "Ustawienia"]) {
      expect(await screen.findByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Subskrypcje" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Diagnostyka" })).not.toBeInTheDocument();
  });

  it("adds diagnostics once developer mode is on", async () => {
    given({ engine: engine({ dev: { verbose: false, developer_mode: true } }) });
    mountApp();
    expect(await screen.findByRole("button", { name: "Diagnostyka" })).toBeInTheDocument();
  });

  it("leaves a route that stops existing rather than showing nothing", async () => {
    given({ engine: engine({ dev: { verbose: false, developer_mode: true } }), view: "developer" });
    const { rerender } = mountApp();
    await waitFor(() => expect(useStore.getState().view).toBe("developer"));

    useStore.setState({ engine: engine({ dev: { verbose: false, developer_mode: false } }) });
    rerender(<App />);
    await waitFor(() => expect(useStore.getState().view).toBe("home"));
  });

  it("collapses to a rail on a narrow window and to an overlay on a tiny one", async () => {
    given({});
    const { unmount } = mountApp(RAIL_BELOW - 1);
    await waitFor(() =>
      expect(document.querySelector('.shell[data-nav="rail"]')).toBeInTheDocument(),
    );
    unmount();

    given({});
    mountApp(OVERLAY_BELOW - 1);
    await waitFor(() =>
      expect(document.querySelector('.shell[data-nav="overlay"]')).toBeInTheDocument(),
    );
    // The composer must still be reachable at the minimum size.
    expect(screen.getByLabelText(/Powiedz, co ma być zrobione/)).toBeInTheDocument();
  });
});

describe("connections", () => {
  it("separates what is available from what needs administrator", async () => {
    given({
      tools: [
        tool({ name: "disk_usage", available: true, effects: ["read"] }),
        tool({ name: "install_msi", available: true, effects: ["install", "elevate"] }),
        tool({ name: "mac_only", available: false, effects: ["read"] }),
      ],
    });
    render(<ConnectionsView />);

    await waitFor(() => expect(screen.getByText(/1 · Dostępne tutaj/)).toBeInTheDocument());
    expect(screen.getByText(/1 · Wymaga administratora/)).toBeInTheDocument();
    expect(screen.getByText(/1 · Nie na tym systemie/)).toBeInTheDocument();
  });

  it("says plainly that it does not know whether a tool ever ran here", async () => {
    given({ tools: [tool()] });
    render(<ConnectionsView />);
    await waitFor(() =>
      expect(screen.getByText(/nie będę udawać, że mierzę/)).toBeInTheDocument(),
    );
  });

  it("shows a device's real last contact and state", async () => {
    given({ devices: [device()] });
    render(<ConnectionsView />);
    await waitFor(() => expect(screen.getByText("produkcja")).toBeInTheDocument());
    expect(screen.getByText("Odpowiada")).toBeInTheDocument();
    expect(screen.getByText(/ostatni kontakt/)).toBeInTheDocument();
  });

  it("says what an empty list means", async () => {
    given({ devices: [] });
    render(<ConnectionsView />);
    await waitFor(() =>
      expect(screen.getByText("Nie znam jeszcze żadnego serwera.")).toBeInTheDocument(),
    );
  });
});

describe("settings", () => {
  it("has a category for everything it can actually change", () => {
    given({});
    render(<SettingsView />);
    for (const label of [
      "Ogólne",
      "Wygląd",
      "Modele",
      "Pamięć i prywatność",
      "Powiadomienia",
      "Bezpieczeństwo",
      "Zaawansowane",
      "Głos",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("sends an appearance change to the engine, not to local state", async () => {
    const patchConfig = vi.fn(async () => ({ paths: ["appearance.theme"] }));
    given({ api: fakeApi({ patchConfig }) });
    render(<SettingsView />);

    await userEvent.click(screen.getByRole("button", { name: "Wygląd" }));
    await userEvent.click(screen.getByRole("radio", { name: "Ciemny" }));

    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith({ "appearance.theme": "dark" }),
    );
  });

  it("offers every accent the engine accepts, plus a custom colour", async () => {
    given({});
    render(<SettingsView />);
    await userEvent.click(screen.getByRole("button", { name: "Wygląd" }));

    for (const name of ["Cyjan", "Fiolet", "Bursztyn", "Róż"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByLabelText("Własny kolor akcentu")).toBeInTheDocument();
  });

  it("reports a refusal instead of silently reverting", async () => {
    given({
      api: fakeApi({
        patchConfig: async () => {
          throw new Error("appearance.theme: „neonowy” nie jest jedną z system, dark, light");
        },
      }),
    });
    render(<SettingsView />);

    await userEvent.click(screen.getByRole("button", { name: "Wygląd" }));
    await userEvent.click(screen.getByRole("radio", { name: "Jasny" }));

    await waitFor(() => expect(screen.getByText(/nie jest jedną z/)).toBeInTheDocument());
  });

  it("says Voice does not work rather than offering dead controls", async () => {
    given({});
    render(<SettingsView />);
    await userEvent.click(screen.getByRole("button", { name: "Głos" }));

    expect(screen.getByText("Jeszcze nie działa.")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("marks the settings whose engine half is missing", async () => {
    given({});
    render(<SettingsView />);

    await userEvent.click(screen.getByRole("button", { name: "Wygląd" }));
    const sounds = screen.getByText("Dźwięki").closest("div");
    expect(within(sounds as HTMLElement).getByText(/jeszcze ich nie odtwarza/)).toBeInTheDocument();
  });

  it("edits the real quiet hours instead of printing them as text", async () => {
    const patchConfig = vi.fn(async () => ({ paths: [] }));
    given({ api: fakeApi({ patchConfig }) });
    render(<SettingsView />);

    await userEvent.click(screen.getByRole("button", { name: "Powiadomienia" }));
    const from = screen.getByLabelText("Cisza od");
    expect(from).toHaveValue("23:00");

    // A time input takes a whole value, not keystrokes.
    fireEvent.change(from, { target: { value: "22:30" } });
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith({ "notifications.quiet_hours.start": "22:30" }),
    );
  });
});
