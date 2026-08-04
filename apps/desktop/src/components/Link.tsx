/**
 * Whether the window can reach the engine, and what to do when it cannot.
 *
 * Split out of the shell because it is the one piece that has to work when
 * nothing else does: every state here is one a person can act on, so each says
 * what happened and offers the action that fixes it, rather than spinning.
 */

import { motion } from "framer-motion";
import { useState } from "react";
import type { Link } from "../lib/api";
import { inTauri, restartEngine } from "../lib/api";
import { AMBIENT, LOOP } from "../lib/motion";
import { useDocumentVisible } from "../lib/useDocumentVisible";

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

export function LinkPill({
  link,
  version,
  onRetry,
}: {
  link: Link;
  version?: string;
  onRetry: () => void;
}) {
  const { word, tone } = LINK_WORDS[link.state];
  // A dot pulsing in a minimised window costs frames and tells nobody anything.
  // The hook is called unconditionally — short-circuiting it behind the link
  // state would be a conditional hook, and React would lose the state entirely
  // the first time the connection dropped.
  const visible = useDocumentVisible();
  const live = link.state === "connected" && visible;
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
        transition={{ duration: AMBIENT.attention, ...LOOP }}
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
export function LinkFailure({ link, onRetry }: { link: Link; onRetry: () => void }) {
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

