/**
 * Adding, replacing and removing a model key — with the save actually visible.
 *
 * The flow the engine already supports and the old screen threw away: `POST
 * /api/vault` rebuilds the provider *and health-checks it* before it answers,
 * so the reply says whether the key works. The screen used to ignore that and
 * show "klucz w sejfie" either way.
 *
 * So the states here are real: editing → saving → checking → online, or the
 * exact failure. Nothing is claimed before the engine has said it.
 */

import { motion } from "framer-motion";
import { useState } from "react";
import type { Provider } from "../lib/api";
import { TWEEN } from "../lib/motion";
import { useStore } from "../lib/store";
import { humanSecretName } from "./Memory";
import { ProviderRow, RecheckButton } from "./Providers";
import { Glass, Section } from "./ui";

/** secret name, label, and the router's name for the same provider. */
const PROVIDERS: [string, string, string][] = [
  ["openai_api_key", "OpenAI", "openai"],
  ["anthropic_api_key", "Claude", "anthropic"],
  ["gemini_api_key", "Gemini", "gemini"],
];

type Phase = "idle" | "editing" | "saving" | "checking" | "done";

const PHASE_TEXT: Record<Phase, string> = {
  idle: "",
  editing: "",
  saving: "Zapisuję…",
  checking: "Sprawdzam u dostawcy…",
  done: "",
};

const unconfigured = (name: string): Provider => ({
  name,
  available: false,
  models: [],
  status: "invalid_config",
  reason: "Brak klucza.",
  detail: "",
  checked_at: 0,
  latency_ms: 0,
  retry_after: 0,
  failures: 0,
});

function ProviderPanel({
  secretName,
  label,
  engineName,
}: {
  secretName: string;
  label: string;
  engineName: string;
}) {
  const api = useStore((s) => s.api);
  const engine = useStore((s) => s.engine);
  const secrets = useStore((s) => s.secrets);
  const refresh = useStore((s) => s.refresh);
  const refreshSecrets = useStore((s) => s.refreshSecrets);

  const [phase, setPhase] = useState<Phase>("idle");
  const [value, setValue] = useState("");
  const [failure, setFailure] = useState("");

  const provider =
    engine?.models.providers.find((candidate) => candidate.name === engineName) ??
    unconfigured(engineName);
  const stored = secrets.some((secret) => secret.name === secretName);

  const save = async () => {
    if (!api || !value.trim()) return;
    setFailure("");
    setPhase("saving");
    try {
      // The engine rebuilds and checks before replying, so "checking" is a real
      // phase of one request rather than a spinner we invented.
      setPhase("checking");
      await api.storeSecret(secretName, value.trim(), "Klucz dostawcy modeli");
      setValue("");
      setPhase("done");
      await Promise.all([refreshSecrets(), refresh()]);
      setTimeout(() => setPhase("idle"), 1200);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "Nie udało się zapisać.");
      setPhase("editing");
    }
  };

  const remove = async () => {
    if (!api) return;
    await api.deleteSecret(secretName);
    await Promise.all([refreshSecrets(), refresh()]);
  };

  const busy = phase === "saving" || phase === "checking";

  return (
    <Glass className="glass--card provider">
      <div className="provider__head">
        <div>
          <strong>{label}</strong>
          <div className="tiny faint">{stored ? humanSecretName(secretName) : "bez klucza"}</div>
        </div>
        <ProviderRow provider={provider} />
      </div>

      {phase === "editing" || (!stored && phase === "idle") ? (
        <div className="provider__form">
          <label className="sr-only" htmlFor={`key-${secretName}`}>
            Klucz {label}
          </label>
          <input
            id={`key-${secretName}`}
            className="field"
            type="password"
            autoComplete="off"
            value={value}
            placeholder="wklej klucz"
            disabled={busy}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void save()}
          />
          <button className="btn tiny" onClick={() => void save()} disabled={!value.trim() || busy}>
            {busy ? PHASE_TEXT[phase] : "Zapisz i sprawdź"}
          </button>
          {phase === "editing" && stored && (
            <button className="btn btn--quiet tiny" onClick={() => setPhase("idle")}>
              Anuluj
            </button>
          )}
        </div>
      ) : (
        <div className="message__actions">
          <button className="btn btn--quiet tiny" onClick={() => setPhase("editing")}>
            {stored ? "Zmień klucz" : "Dodaj klucz"}
          </button>
          {stored && (
            <button className="btn btn--quiet tiny" onClick={() => void remove()}>
              Usuń klucz
            </button>
          )}
        </div>
      )}

      {busy && (
        <motion.span
          className="tiny"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={TWEEN.control}
          style={{ color: "var(--accent)" }}
          role="status"
        >
          {PHASE_TEXT[phase]}
        </motion.span>
      )}
      {phase === "done" && (
        <span className="tiny" style={{ color: "hsl(var(--state-ok))" }} role="status">
          Zapisane i sprawdzone.
        </span>
      )}
      {failure && (
        <span className="tiny" style={{ color: "hsl(var(--state-error))" }} role="alert">
          {failure}
        </span>
      )}

      {provider.models.length > 0 && (
        <details className="provider__models">
          <summary className="tiny faint">{provider.models.length} modeli</summary>
          <div className="tiny faint selectable">{provider.models.join(", ")}</div>
        </details>
      )}
    </Glass>
  );
}

export function ModelsSettings() {
  return (
    <Section title="Modele" action={<RecheckButton />}>
      <p className="tiny soft">
        Nie wybierasz modelu do zadania — dobieram go sam. Podaj klucze, a resztą
        się zajmę. Klucz trafia do sejfu, nie do ustawień, i nigdy go nie pokażę.
      </p>
      <div className="memory-list">
        {PROVIDERS.map(([secretName, label, engineName]) => (
          <ProviderPanel
            key={secretName}
            secretName={secretName}
            label={label}
            engineName={engineName}
          />
        ))}
      </div>
    </Section>
  );
}
