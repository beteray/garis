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
import type { Link } from "./lib/api";
import { GarisApi, classify, discover, inTauri, restartEngine } from "./lib/api";
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
  // One selector per value. `useStore()` without a selector subscribes to the
  // whole store, so every progress line from every running task would re-render
  // the entire tree — the exact opposite of the 60 fps requirement.
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const agentState = useStore((s) => s.agentState);
  const level = useStore((s) => s.level);
  const engine = useStore((s) => s.engine);
  const connection = useStore((s) => s.connection);
  const approvals = useStore((s) => s.approvals);
  const tasks = useStore((s) => s.tasks);
  const setApi = useStore((s) => s.setApi);
  const handleEvent = useStore((s) => s.handleEvent);
  const setConnection = useStore((s) => s.setConnection);
  const refresh = useStore((s) => s.refresh);

  const [ready, setReady] = useState(false);
  const [onboarding, setOnboarding] = useState(false);
  const [link, setLink] = useState<Link>({ state: "starting" });
  const [attempt, setAttempt] = useState(0);
  const reduced = prefersReducedMotion();

  useEffect(() => {
    let disconnect: (() => void) | undefined;
    let cancelled = false;

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
    setLink((current) => {
      if (connection === "open") return { state: "connected" };
      if (current.state === "connected") return { state: "retrying", attempt: 1 };
      return current;
    });
  }, [connection]);

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

        <LinkPill link={link} version={engine?.version} onRetry={() => setAttempt((n) => n + 1)} />
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
              ) : link.state !== "connected" && link.state !== "retrying" ? (
                <LinkFailure link={link} onRetry={() => setAttempt((n) => n + 1)} />
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

/** One line, one dot, one truth about whether the agent is reachable. */
const LINK_WORDS: Record<Link["state"], { word: string; tone: string }> = {
  starting: { word: "uruchamiam silnik…", tone: "var(--state-busy)" },
  handshake: { word: "przedstawiamy się…", tone: "var(--state-busy)" },
  connected: { word: "", tone: "var(--state-ok)" },
  "engine-failed": { word: "silnik nie wystartował", tone: "var(--state-error)" },
  "bad-token": { word: "token odrzucony", tone: "var(--state-error)" },
  "no-response": { word: "silnik nie odpowiada", tone: "var(--state-error)" },
  retrying: { word: "ponawiam…", tone: "var(--state-warn)" },
};

function LinkPill({
  link,
  version,
  onRetry,
}: {
  link: Link;
  version?: string;
  onRetry: () => void;
}) {
  const { word, tone } = LINK_WORDS[link.state];
  const live = link.state === "connected";
  return (
    <button
      onClick={onRetry}
      disabled={live}
      className="btn btn--quiet no-drag tiny"
      title={live ? "Połączono" : "Kliknij, żeby spróbować ponownie"}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 7,
        padding: "6px 12px",
        justifyContent: "flex-start",
        opacity: live ? 0.7 : 1,
        cursor: live ? "default" : "pointer",
      }}
    >
      <motion.span
        animate={{
          opacity: live ? [0.6, 1, 0.6] : 1,
          scale: live ? [1, 1.25, 1] : 1,
        }}
        transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        style={{ width: 7, height: 7, borderRadius: "50%", background: `hsl(${tone})`, flexShrink: 0 }}
      />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {live ? `v${version ?? "?"}` : word}
      </span>
    </button>
  );
}

/**
 * What the stage shows when there is no engine behind it.
 *
 * Deliberately not a spinner: every state here is one a person can act on, so
 * each one says what happened and offers the action that fixes it.
 */
function LinkFailure({ link, onRetry }: { link: Link; onRetry: () => void }) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const detail = "detail" in link ? link.detail : "";

  const report = [
    `stan: ${link.state}`,
    `szczegóły: ${detail || "—"}`,
    `powłoka: ${inTauri() ? "Tauri" : "przeglądarka"}`,
    `adres: ${window.location.href}`,
    `czas: ${new Date().toISOString()}`,
  ].join("\n");

  const restart = async () => {
    setBusy(true);
    try {
      if (inTauri()) await restartEngine();
    } finally {
      setBusy(false);
      onRetry();
    }
  };

  return (
    <div
      style={{
        height: "100%",
        display: "grid",
        placeContent: "center",
        justifyItems: "center",
        gap: 16,
        textAlign: "center",
        padding: 24,
        maxWidth: 520,
        margin: "0 auto",
      }}
    >
      <div style={{ fontSize: 34, opacity: 0.5 }}>◍</div>
      <div style={{ fontSize: 17, fontWeight: 550 }}>{LINK_WORDS[link.state].word}</div>
      {detail && <p className="soft" style={{ margin: 0, lineHeight: 1.5 }}>{detail}</p>}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
        <button className="btn" onClick={onRetry} disabled={busy}>
          Spróbuj ponownie
        </button>
        {inTauri() && (
          <button className="btn" onClick={restart} disabled={busy}>
            {busy ? "Uruchamiam…" : "Uruchom silnik ponownie"}
          </button>
        )}
        <button
          className="btn btn--quiet"
          onClick={() => {
            void navigator.clipboard.writeText(report).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            });
          }}
        >
          {copied ? "Skopiowano" : "Skopiuj diagnostykę"}
        </button>
      </div>

      <p className="tiny faint" style={{ margin: 0, lineHeight: 1.5 }}>
        Log silnika:&nbsp;
        <code>%LOCALAPPDATA%\ai.garis.desktop\logs\engine.log</code>
      </p>
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
