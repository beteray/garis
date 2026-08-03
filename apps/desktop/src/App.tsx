/**
 * The shell.
 *
 * A single window that never blocks the work: closing it hides it, the engine
 * carries on, and reopening shows the true state because the first WebSocket
 * frame is a full snapshot.
 *
 * The shell owns three things and delegates everything else: which route is
 * showing, what shape the navigation is in, and what GARIS is currently doing.
 * That last one is computed in `lib/runtimeState.ts` from measured facts and
 * passed down, so no screen derives its own idea of the agent's state.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import type { Link } from "./lib/api";
import { GarisApi, classify, discover } from "./lib/api";
import { applyAppearance, watchSystemAppearance } from "./lib/appearance";
import { variants } from "./lib/motion";
import { navShape, routesFor } from "./lib/nav";
import type { Route } from "./lib/nav";
import { runtimeState } from "./lib/runtimeState";
import { useStore } from "./lib/store";
import { useWindowSize } from "./lib/useWindowSize";
import { ApprovalLayer } from "./components/Approvals";
import { ConnectionsView } from "./components/Connections";
import { DeveloperView } from "./components/Developer";
import { Home } from "./components/Home";
import { LinkFailure, LinkPill } from "./components/Link";
import { MemoryView } from "./components/Memory";
import { Nav, NavButton } from "./components/Nav";
import { Onboarding } from "./components/Onboarding";
import { Presence } from "./components/Presence";
import { SettingsView } from "./components/Settings";
import { TasksView } from "./components/Tasks";
import { Glass } from "./components/ui";
import * as fixture from "./dev/fixture";

export default function App() {
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const agentState = useStore((s) => s.agentState);
  const engine = useStore((s) => s.engine);
  const connection = useStore((s) => s.connection);
  const approvals = useStore((s) => s.approvals);
  const tasks = useStore((s) => s.tasks);
  const sending = useStore((s) => s.sending);
  const setApi = useStore((s) => s.setApi);
  const handleEvent = useStore((s) => s.handleEvent);
  const setConnection = useStore((s) => s.setConnection);
  const refresh = useStore((s) => s.refresh);

  const [ready, setReady] = useState(false);
  const [onboarding, setOnboarding] = useState(false);
  const [link, setLink] = useState<Link>({ state: "starting" });
  const [attempt, setAttempt] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const { width } = useWindowSize();

  useEffect(() => {
    let disconnect: (() => void) | undefined;
    let cancelled = false;

    // A visual fixture is already in the store, and there is nothing to
    // discover: reaching for an engine here would either hang or, worse,
    // overwrite the snapshot with real data half-way through a screenshot.
    if (fixture.mounted()) {
      setLink(fixture.link());
      setReady(true);
      return;
    }

    void (async () => {
      setLink({ state: "starting" });
      const found = await discover();
      if (cancelled) return;

      // The shell knows it failed to launch anything. Say so, rather than
      // spending thirty seconds pretending to connect to nothing.
      if (found.fromShell && !found.token) {
        setLink({
          state: "engine-failed",
          detail: found.error || "Powłoka nie zdołała uruchomić silnika.",
        });
        setReady(true);
        return;
      }

      setLink({ state: "handshake" });
      const api = new GarisApi(found.base, found.token);
      setApi(api);

      try {
        await api.health();
        await refresh();
        if (cancelled) return;
        setLink({ state: "connected" });
      } catch (cause) {
        if (cancelled) return;
        setLink(classify(cause));
      }
      setReady(true);
      disconnect = api.connect(handleEvent, setConnection);
    })();

    return () => {
      cancelled = true;
      disconnect?.();
    };
  }, [setApi, handleEvent, setConnection, refresh, attempt]);

  // The socket is the live truth once we are up: losing it is "retrying", not
  // "connected", and getting it back clears whatever error came before.
  useEffect(() => {
    // Under a fixture there is no socket, and "no socket" must not be reported
    // as a lost connection — the scenario already said what the pill shows.
    if (fixture.mounted()) return;
    setLink((current) => {
      if (connection === "open") return { state: "connected" };
      if (current.state === "connected") return { state: "retrying", attempt: 1 };
      return current;
    });
  }, [connection]);

  useEffect(() => {
    if (engine && !engine.identity.onboarded) setOnboarding(true);
  }, [engine]);

  // The machine can change its mind while the window is open — the desktop
  // theme flips at sunset, or someone turns on "reduce motion" mid-task.
  useEffect(
    () => watchSystemAppearance(() => applyAppearance(engine?.appearance ?? {})),
    [engine],
  );

  const destinations = routesFor(engine?.dev.developer_mode ?? false);
  const shape = navShape(engine?.appearance.navigation ?? "auto", width);

  // A route that stops existing (developer mode turned off) must not leave the
  // window on a blank screen.
  useEffect(() => {
    if (!destinations.some((destination) => destination.id === view)) setView("home");
  }, [destinations, view, setView]);

  const state = useMemo(
    () =>
      runtimeState({
        link,
        agent: agentState,
        sending,
        pendingApprovals: approvals.length,
        providers: engine?.models.providers ?? [],
        quietHours: engine?.notifications.quiet_hours ?? null,
      }),
    [link, agentState, sending, approvals.length, engine],
  );

  const badges: Partial<Record<Route, number>> = {
    tasks: tasks.filter((task) => task.state === "blocked").length,
  };
  const pending = Object.values(badges).reduce((total, value) => total + (value ?? 0), 0);

  const header = (
    <div className="shell__brand">
      <Presence state={state} size={9} compact />
      <div className="shell__brand-text">
        <strong>GARIS</strong>
        <span className="tiny soft">{engine?.version ? `v${engine.version}` : "…"}</span>
      </div>
    </div>
  );

  const footer = (
    <LinkPill link={link} version={engine?.version} onRetry={() => setAttempt((n) => n + 1)} />
  );

  return (
    <div className="shell" data-nav={shape}>
      {shape !== "overlay" && (
        <Nav
          shape={shape}
          destinations={destinations}
          current={view}
          onNavigate={setView}
          badges={badges}
          header={header}
          footer={footer}
        />
      )}
      {shape === "overlay" && (
        <Nav
          shape="overlay"
          destinations={destinations}
          current={view}
          onNavigate={setView}
          badges={badges}
          open={menuOpen}
          onClose={() => setMenuOpen(false)}
          header={header}
          footer={footer}
        />
      )}

      <div className="shell__work">
        {shape === "overlay" && (
          <div className="shell__topbar glass drag-region">
            <NavButton onOpen={() => setMenuOpen(true)} pending={pending} />
            <Presence state={state} size={9} />
            <div style={{ flex: 1 }} />
            <LinkPill
              link={link}
              version={engine?.version}
              onRetry={() => setAttempt((n) => n + 1)}
            />
          </div>
        )}

        <AnimatePresence mode="wait">
          <motion.main
            key={view}
            variants={variants("panel")}
            initial="hidden"
            animate="visible"
            exit="exit"
            className="shell__stage"
            // The route is announced, so a screen-reader user knows the view
            // changed without hunting for what moved.
            aria-label={destinations.find((d) => d.id === view)?.label}
          >
            <Glass className="shell__panel">
              {!ready ? (
                <div className="skeleton" style={{ height: "100%" }} />
              ) : link.state !== "connected" && link.state !== "retrying" ? (
                <LinkFailure link={link} onRetry={() => setAttempt((n) => n + 1)} />
              ) : view === "home" ? (
                <Home state={state} />
              ) : view === "tasks" ? (
                <TasksView />
              ) : view === "memory" ? (
                <MemoryView />
              ) : view === "connections" ? (
                <ConnectionsView />
              ) : view === "settings" ? (
                <SettingsView />
              ) : (
                <DeveloperView />
              )}
            </Glass>
          </motion.main>
        </AnimatePresence>
      </div>

      <ApprovalLayer />

      <AnimatePresence>
        {onboarding && <Onboarding onFinish={() => setOnboarding(false)} />}
      </AnimatePresence>
    </div>
  );
}
