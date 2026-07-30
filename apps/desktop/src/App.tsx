/**
 * The shell.
 *
 * A single window that never blocks the work: closing it hides it, the engine
 * carries on, and reopening shows the true state because the first WebSocket
 * frame is a full snapshot.
 *
 * View changes use a shared layout, so panels move rather than blink.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { GarisApi, discover } from "./lib/api";
import {
  EASE,
  base,
  panelVariants,
  prefersReducedMotion,
  quick,
  reducedPanelVariants,
  spring,
} from "./lib/motion";
import type { View } from "./lib/store";
import { useStore } from "./lib/store";
import { ApprovalBadge, ApprovalLayer } from "./components/Approvals";
import { Composer } from "./components/Composer";
import { Conversation } from "./components/Conversation";
import { Onboarding } from "./components/Onboarding";
import { DevicesView, DiagnosticsView, MemoryView, SubscriptionsView } from "./components/Panels";
import { Orb, STATE_LABELS } from "./components/Orb";
import { SettingsView } from "./components/Settings";
import { TasksView } from "./components/Tasks";
import { AnimatedNumber, Glass } from "./components/ui";

const NAV: [View, string, string][] = [
  ["home", "Start", "◎"],
  ["conversation", "Rozmowa", "💬"],
  ["tasks", "Zadania", "◷"],
  ["memory", "Pamięć", "🧠"],
  ["devices", "Urządzenia", "🖥"],
  ["subscriptions", "Subskrypcje", "✦"],
  ["settings", "Ustawienia", "⚙"],
];

export default function App() {
  const {
    view,
    setView,
    agentState,
    level,
    engine,
    connection,
    approvals,
    tasks,
    setApi,
    handleEvent,
    setConnection,
    refresh,
  } = useStore();

  const [ready, setReady] = useState(false);
  const [onboarding, setOnboarding] = useState(false);
  const reduced = prefersReducedMotion();

  useEffect(() => {
    let disconnect: (() => void) | undefined;

    void (async () => {
      const { base: url, token } = await discover();
      const api = new GarisApi(url, token);
      setApi(api);
      try {
        await api.health();
        await refresh();
        setReady(true);
      } catch {
        setReady(true); // show the disconnected state rather than a blank window
      }
      disconnect = api.connect(handleEvent, setConnection);
    })();

    return () => disconnect?.();
  }, [setApi, handleEvent, setConnection, refresh]);

  useEffect(() => {
    if (engine && !engine.identity.onboarded) setOnboarding(true);
  }, [engine]);

  const activeCount = tasks.filter((task) =>
    ["pending", "running", "blocked"].includes(task.state),
  ).length;

  return (
    <div style={{ display: "flex", height: "100%", padding: 14, gap: 14 }}>
      {/* ---------------------------------------------------------- navigation */}
      <Glass
        className="drag-region"
        style={{
          width: "var(--nav-width)",
          padding: 16,
          display: "flex",
          flexDirection: "column",
          gap: 6,
          flexShrink: 0,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "4px 8px 14px",
          }}
        >
          <Orb state={agentState} level={level} size={34} />
          <div style={{ display: "grid" }}>
            <strong style={{ letterSpacing: "0.04em" }}>GARIS</strong>
            {/* The status word changes with the orb — one truth, two renderings. */}
            <AnimatePresence mode="wait">
              <motion.span
                key={agentState}
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={quick}
                className="tiny soft"
              >
                {STATE_LABELS[agentState]}
              </motion.span>
            </AnimatePresence>
          </div>
        </div>

        {NAV.map(([id, label, icon]) => {
          const active = view === id;
          return (
            <button
              key={id}
              onClick={() => setView(id)}
              className="btn btn--quiet no-drag"
              style={{
                position: "relative",
                justifyContent: "flex-start",
                gap: 11,
                padding: "9px 12px",
                color: active ? "var(--ink)" : "var(--ink-soft)",
              }}
            >
              {/* The selection pill slides between items — the same element,
                  moved, not one hidden and another shown. */}
              {active && (
                <motion.span
                  layoutId="nav-active"
                  transition={spring}
                  style={{
                    position: "absolute",
                    inset: 0,
                    borderRadius: "var(--radius-control)",
                    background: "var(--glass-bg-strong)",
                    border: "1px solid var(--glass-stroke)",
                  }}
                />
              )}
              <span style={{ position: "relative", zIndex: 1 }}>{icon}</span>
              <span style={{ position: "relative", zIndex: 1, flex: 1, textAlign: "left" }}>
                {label}
              </span>
              {id === "tasks" && activeCount > 0 && (
                <span className="tiny" style={{ position: "relative", zIndex: 1 }}>
                  <AnimatedNumber value={activeCount} />
                </span>
              )}
              {id === "settings" && (
                <span style={{ position: "relative", zIndex: 1 }}>
                  <ApprovalBadge count={approvals.length} />
                </span>
              )}
            </button>
          );
        })}

        <div style={{ flex: 1 }} />

        <button
          onClick={() => setView("diagnostics")}
          className="btn btn--quiet no-drag tiny"
          style={{ justifyContent: "flex-start", gap: 11, padding: "8px 12px" }}
        >
          <span>◔</span> Diagnostyka
        </button>

        <div
          className="tiny faint"
          style={{ display: "flex", alignItems: "center", gap: 7, padding: "6px 12px" }}
        >
          <motion.span
            animate={{
              opacity: connection === "open" ? [0.6, 1, 0.6] : 0.5,
              scale: connection === "open" ? [1, 1.25, 1] : 1,
            }}
            transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background:
                connection === "open"
                  ? "hsl(var(--state-ok))"
                  : "hsl(var(--state-error))",
            }}
          />
          {connection === "open" ? `v${engine?.version ?? "?"}` : "łączę się…"}
        </div>
      </Glass>

      {/* --------------------------------------------------------------- stage */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            variants={reduced ? reducedPanelVariants : panelVariants}
            initial="hidden"
            animate="visible"
            exit="exit"
            style={{ flex: 1, minHeight: 0, display: "flex" }}
          >
            <Glass
              className="scroll"
              style={{ flex: 1, padding: "var(--pad-panel)", minHeight: 0 }}
            >
              {!ready ? (
                <div className="skeleton" style={{ height: "100%" }} />
              ) : view === "home" ? (
                <HomeView />
              ) : view === "conversation" ? (
                <Conversation />
              ) : view === "tasks" ? (
                <TasksView />
              ) : view === "memory" ? (
                <MemoryView />
              ) : view === "devices" ? (
                <DevicesView />
              ) : view === "subscriptions" ? (
                <SubscriptionsView />
              ) : view === "settings" ? (
                <SettingsView />
              ) : (
                <DiagnosticsView />
              )}
            </Glass>
          </motion.div>
        </AnimatePresence>

        <Composer />
      </div>

      <ApprovalLayer />

      <AnimatePresence>
        {onboarding && <Onboarding onFinish={() => setOnboarding(false)} />}
      </AnimatePresence>
    </div>
  );
}

function HomeView() {
  const agentState = useStore((s) => s.agentState);
  const level = useStore((s) => s.level);
  const engine = useStore((s) => s.engine);
  const tasks = useStore((s) => s.tasks);
  const chat = useStore((s) => s.chat);

  const active = tasks.filter((task) =>
    ["pending", "running", "blocked"].includes(task.state),
  );

  return (
    <div
      style={{
        height: "100%",
        display: "grid",
        gridTemplateRows: "1fr auto",
        placeItems: "center",
        gap: 18,
      }}
    >
      <div style={{ display: "grid", placeItems: "center", gap: 14 }}>
        <motion.div
          initial={{ scale: 0.85, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ ...spring, delay: 0.05 }}
        >
          <Orb state={agentState} level={level} size={300} />
        </motion.div>

        <AnimatePresence mode="wait">
          <motion.div
            key={agentState}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={base}
            style={{ textAlign: "center", display: "grid", gap: 4 }}
          >
            <div style={{ fontSize: 17, fontWeight: 550 }}>
              {greeting(engine?.identity.address_as)}
            </div>
            <div className="soft tiny">
              {active.length > 0
                ? `${active.length} ${active.length === 1 ? "zadanie" : "zadania"} w toku`
                : STATE_LABELS[agentState]}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>

      {chat.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ ...base, delay: 0.1 }}
          style={{ width: "100%", maxWidth: 720 }}
        >
          <Conversation compact />
        </motion.div>
      )}
    </div>
  );
}

function greeting(name?: string): string {
  const hour = new Date().getHours();
  const part =
    hour < 5 ? "Dobrej nocy" : hour < 12 ? "Dzień dobry" : hour < 18 ? "Cześć" : "Dobry wieczór";
  return name ? `${part}, ${name}.` : `${part}.`;
}

export const appEase = EASE;
