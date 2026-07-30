/**
 * Settings.
 *
 * Only things a person actually has an opinion about: their name, how GARIS talks
 * to them, quiet hours, which voice, what it may spend. There is deliberately no
 * "which model for which task" table and no per-tool permission grid — those are
 * the runtime's job, and exposing them would undo the product's premise.
 */

import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import { base, quick, staggerContainer } from "../lib/motion";
import { useStore } from "../lib/store";
import { Glass, Pill, Section } from "./ui";

const PERSONAS = [
  ["assistant", "Asystent", "Rzeczowy, konkretny."],
  ["kolega", "Kolega", "Luźniej, z humorem."],
  ["butler", "Kamerdyner", "Formalnie i uprzejmie."],
  ["cichy", "Cichy", "Minimum słów."],
] as const;

const PROVIDERS = [
  ["openai_api_key", "OpenAI"],
  ["anthropic_api_key", "Claude"],
  ["gemini_api_key", "Gemini"],
] as const;

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
        padding: "11px 0",
      }}
    >
      <div style={{ display: "grid", gap: 2 }}>
        <span>{label}</span>
        {hint && <span className="tiny faint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

function Toggle({
  on,
  onChange,
}: {
  on: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <button
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      style={{
        width: 48,
        height: 28,
        padding: 3,
        border: "1px solid var(--glass-stroke)",
        borderRadius: "var(--radius-pill)",
        background: on ? "hsl(var(--state-working) / 0.34)" : "var(--glass-bg)",
        cursor: "pointer",
        display: "flex",
        justifyContent: on ? "flex-end" : "flex-start",
        transition: "background var(--dur-base) var(--ease-glass)",
      }}
    >
      {/* layout animation does the sliding: the knob travels, never teleports */}
      <motion.span
        layout
        transition={{ type: "spring", stiffness: 480, damping: 32 }}
        style={{
          width: 20,
          height: 20,
          borderRadius: "50%",
          background: "var(--ink)",
          display: "block",
        }}
      />
    </button>
  );
}

export function SettingsView() {
  const engine = useStore((s) => s.engine);
  const api = useStore((s) => s.api);
  const refresh = useStore((s) => s.refresh);
  const refreshSecrets = useStore((s) => s.refreshSecrets);
  const secrets = useStore((s) => s.secrets);

  const [name, setName] = useState("");
  const [wakeWord, setWakeWord] = useState("garis");
  const [keyFor, setKeyFor] = useState<string | null>(null);
  const [keyValue, setKeyValue] = useState("");
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    if (!engine) return;
    setName(engine.identity.address_as || engine.identity.name);
    setWakeWord(engine.voice.wake_word);
  }, [engine]);

  useEffect(() => {
    void refreshSecrets();
  }, [refreshSecrets]);

  const patch = async (changes: Record<string, unknown>) => {
    if (!api) return;
    await api.patchConfig(changes);
    setSaved(Object.keys(changes)[0] ?? null);
    setTimeout(() => setSaved(null), 1600);
    await refresh();
  };

  const saveKey = async () => {
    if (!api || !keyFor || !keyValue.trim()) return;
    await api.storeSecret(keyFor, keyValue.trim(), "Klucz dostawcy modeli");
    setKeyValue("");
    setKeyFor(null);
    await refreshSecrets();
    await refresh();
  };

  const has = (name: string) => secrets.some((secret) => secret.name === name);

  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="visible"
      style={{ display: "grid", gap: 20 }}
    >
      <Section title="Ty">
        <Glass style={{ padding: "6px 16px", borderRadius: "var(--radius-card)" }}>
          <Row label="Jak mam się do Ciebie zwracać" hint="Np. imieniem albo „szefie”">
            <input
              className="field"
              style={{ width: 200 }}
              value={name}
              onChange={(event) => setName(event.target.value)}
              onBlur={() => void patch({ "identity.address_as": name })}
            />
          </Row>
          <Row label="Język" hint="W tym języku mówię i piszę">
            <span className="soft">{engine?.identity.language ?? "pl"}</span>
          </Row>
        </Glass>
      </Section>

      <Section title="Osobowość">
        <p className="tiny soft">
          Zmienia ton, humor i długość wypowiedzi. Nigdy nie zmienia zasad
          bezpieczeństwa ani sposobu wykonywania zadań.
        </p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {PERSONAS.map(([value, label, hint]) => (
            <Pill
              key={value}
              active={engine?.persona.preset === value}
              onClick={() => void patch({ "persona.preset": value })}
            >
              <span title={hint}>{label}</span>
            </Pill>
          ))}
        </div>
      </Section>

      <Section title="Głos">
        <Glass style={{ padding: "6px 16px", borderRadius: "var(--radius-card)" }}>
          <Row label="Rozmowa głosowa">
            <Toggle
              on={engine?.voice.enabled ?? true}
              onChange={(value) => void patch({ "voice.enabled": value })}
            />
          </Row>
          <Row label="Słowo aktywacyjne" hint="Powiedz je, żeby mnie zawołać">
            <input
              className="field"
              style={{ width: 160 }}
              value={wakeWord}
              onChange={(event) => setWakeWord(event.target.value)}
              onBlur={() => void patch({ "voice.wake_word": wakeWord })}
            />
          </Row>
          <Row label="Nasłuchiwanie słowa aktywacyjnego" hint="Działa lokalnie, nic nie wysyłam">
            <Toggle
              on={engine?.voice.wake_word_enabled ?? true}
              onChange={(value) => void patch({ "voice.wake_word_enabled": value })}
            />
          </Row>
          <Row label="Push-to-talk">
            <code className="mono soft">{engine?.voice.push_to_talk}</code>
          </Row>
        </Glass>
      </Section>

      <Section title="Modele">
        <p className="tiny soft">
          Nie wybierasz modelu do zadania — dobieram go sam. Podaj klucze, a resztą
          się zajmę.
        </p>
        <Glass style={{ padding: "6px 16px", borderRadius: "var(--radius-card)" }}>
          {PROVIDERS.map(([secretName, label]) => (
            <Row
              key={secretName}
              label={label}
              hint={has(secretName) ? "klucz w sejfie" : "brak klucza"}
            >
              {keyFor === secretName ? (
                <span style={{ display: "flex", gap: 6 }}>
                  <input
                    className="field"
                    style={{ width: 220 }}
                    type="password"
                    autoFocus
                    value={keyValue}
                    placeholder="wklej klucz"
                    onChange={(event) => setKeyValue(event.target.value)}
                    onKeyDown={(event) => event.key === "Enter" && void saveKey()}
                  />
                  <button className="btn tiny" onClick={() => void saveKey()}>
                    Zapisz
                  </button>
                </span>
              ) : (
                <button className="btn tiny" onClick={() => setKeyFor(secretName)}>
                  {has(secretName) ? "Zmień" : "Dodaj klucz"}
                </button>
              )}
            </Row>
          ))}
        </Glass>
      </Section>

      <Section title="Zachowanie">
        <Glass style={{ padding: "6px 16px", borderRadius: "var(--radius-card)" }}>
          <Row label="Szczegółowe raporty" hint="Pokazuj kroki, nie tylko wynik">
            <Toggle
              on={engine?.dev.verbose ?? false}
              onChange={(value) => void patch({ "dev.verbose": value })}
            />
          </Row>
          <Row label="Tryb developerski" hint="Plany, prompty, surowe wyniki narzędzi">
            <Toggle
              on={engine?.dev.developer_mode ?? false}
              onChange={(value) => void patch({ "dev.developer_mode": value })}
            />
          </Row>
        </Glass>
      </Section>

      {/* Saving is silent but never invisible: a small confirmation that fades. */}
      <motion.div
        initial={false}
        animate={{ opacity: saved ? 1 : 0, y: saved ? 0 : 6 }}
        transition={base}
        className="tiny"
        style={{ color: "hsl(var(--state-ok))", height: 18 }}
      >
        {saved ? "Zapisane." : ""}
      </motion.div>
    </motion.div>
  );
}

export const settingsTransition = quick;
