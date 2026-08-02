/**
 * What each model provider is actually doing, in the user's words.
 *
 * The failure this replaces: the screen said "klucz w sejfie" and meant it —
 * a completely invalid key looked identical to a working one, and the first
 * anybody heard about it was a task failing an hour later. The engine has
 * measured seven distinct states since 0.1.1; the window was still rendering a
 * boolean.
 *
 * Two rules hold here. The sentence comes from the engine (`reason`), already
 * in Polish and already true — the window does not invent its own wording, or
 * the two drift and one of them is wrong. And nothing technical is shown by
 * default: `detail` carries HTTP codes and provider messages, and lives behind
 * developer mode. Key values and `vault://` references never appear at all.
 */

import { motion } from "framer-motion";
import { useState } from "react";
import type { Provider, ProviderStatus } from "../lib/api";
import { quick } from "../lib/motion";
import { useStore } from "../lib/store";

/** Colour and shape per state. Never colour alone: a dot that differs only in
 *  hue says nothing to a person who cannot separate those hues. */
const LOOK: Record<ProviderStatus, { tone: string; mark: string; label: string }> = {
  online: { tone: "var(--state-ok)", mark: "●", label: "Gotowy" },
  unknown: { tone: "var(--state-busy)", mark: "◌", label: "Sprawdzam" },
  invalid_config: { tone: "var(--state-idle)", mark: "○", label: "Nieskonfigurowany" },
  invalid_key: { tone: "var(--state-error)", mark: "✕", label: "Zły klucz" },
  rate_limit: { tone: "var(--state-warn)", mark: "◐", label: "Limit" },
  timeout: { tone: "var(--state-warn)", mark: "◔", label: "Zwolnił" },
  offline: { tone: "var(--state-error)", mark: "✕", label: "Nie odpowiada" },
};

const UNKNOWN = LOOK.unknown;

/** What the person can do about it. One action, or none — a list of options is
 *  what you offer when you do not know which one is right. */
function advice(provider: Provider): string {
  switch (provider.status) {
    case "invalid_config":
      return "Dodaj klucz poniżej.";
    case "invalid_key":
      return "Popraw klucz — ten został odrzucony.";
    case "rate_limit":
      return provider.retry_after > 0
        ? `Spróbuję ponownie za ${Math.ceil(provider.retry_after / 60)} min.`
        : "To nie minie samo — sprawdź konto u dostawcy.";
    case "timeout":
    case "offline":
      return "Sprawdź połączenie i spróbuj ponownie.";
    default:
      return "";
  }
}

const ago = (moment: number): string => {
  if (!moment) return "jeszcze nie sprawdzałem";
  const seconds = Math.max(0, Date.now() / 1000 - moment);
  if (seconds < 90) return "sprawdzone przed chwilą";
  if (seconds < 5400) return `sprawdzone ${Math.round(seconds / 60)} min temu`;
  return `sprawdzone ${Math.round(seconds / 3600)} godz. temu`;
};

export function ProviderRow({ provider }: { provider: Provider }) {
  const developer = useStore((s) => s.engine?.dev.developer_mode ?? false);
  const look = LOOK[provider.status] ?? UNKNOWN;

  return (
    <div style={{ display: "grid", gap: 3 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span aria-hidden style={{ color: look.tone }}>
          {look.mark}
        </span>
        {/* The state has a word as well as a colour, so it survives being
            printed, screenshotted, or read out. */}
        <span className="tiny" style={{ color: look.tone }}>
          {look.label}
        </span>
        <span className="tiny faint">· {ago(provider.checked_at)}</span>
      </div>
      {/* The engine's sentence, not ours. */}
      <span className="tiny soft">{provider.reason}</span>
      {advice(provider) && <span className="tiny faint">{advice(provider)}</span>}
      {developer && provider.detail && (
        <span className="tiny faint selectable" style={{ fontFamily: "monospace" }}>
          {provider.detail}
        </span>
      )}
    </div>
  );
}

/** "Sprawdź ponownie" — because a state measured an hour ago is a state the
 *  user is entitled to distrust. */
export function RecheckButton() {
  const api = useStore((s) => s.api);
  const refresh = useStore((s) => s.refresh);
  const [busy, setBusy] = useState(false);

  return (
    <button
      className="btn tiny no-drag"
      disabled={busy}
      onClick={async () => {
        if (!api) return;
        setBusy(true);
        try {
          await api.checkProviders();
          await refresh();
        } finally {
          setBusy(false);
        }
      }}
    >
      <motion.span
        animate={{ opacity: busy ? 0.6 : 1 }}
        transition={quick}
      >
        {busy ? "Sprawdzam…" : "Sprawdź ponownie"}
      </motion.span>
    </button>
  );
}

export { LOOK as PROVIDER_LOOK };
