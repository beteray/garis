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
import type { Provider } from "../lib/api";
import { ACCENTS, DEFAULT_APPEARANCE } from "../lib/appearance";
import { base, quick, staggerContainer } from "../lib/motion";
import { useStore } from "../lib/store";
import { ProviderRow, RecheckButton } from "./Providers";
import { Glass, Pill, Section } from "./ui";

const PERSONAS = [
  ["assistant", "Asystent", "Rzeczowy, konkretny."],
  ["kolega", "Kolega", "Luźniej, z humorem."],
  ["butler", "Kamerdyner", "Formalnie i uprzejmie."],
  ["cichy", "Cichy", "Minimum słów."],
] as const;

/** secret name, label, and the router's name for the same provider — the three
 *  have to be paired somewhere, and pairing them here beats guessing per row. */
const PROVIDERS = [
  ["openai_api_key", "OpenAI", "openai"],
  ["anthropic_api_key", "Claude", "anthropic"],
  ["gemini_api_key", "Gemini", "gemini"],
] as const;

function Row({
  label,
  hint,
  status,
  children,
}: {
  label: string;
  hint?: string;
  /** A measured provider state, rendered in full instead of a one-line hint. */
  status?: Provider;
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
      <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
        <span>{label}</span>
        {hint && <span className="tiny faint">{hint}</span>}
        {status && <ProviderRow provider={status} />}
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

/** A short, mutually exclusive set. Segmented rather than a dropdown: every
 *  option is visible, which is what makes "co to zmieni" answerable. */
function Choice({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly (readonly [string, string])[];
  onChange: (value: string) => void;
}) {
  return (
    <div role="radiogroup" style={{ display: "flex", gap: 4 }}>
      {options.map(([id, label]) => (
        <button
          key={id}
          role="radio"
          aria-checked={value === id}
          onClick={() => onChange(id)}
          className="btn btn--quiet no-drag"
          style={{
            padding: "5px 11px",
            fontSize: "var(--text-small)",
            background: value === id ? "var(--accent-soft)" : "transparent",
            borderColor: value === id ? "var(--accent-line)" : "transparent",
            color: value === id ? "var(--ink)" : "var(--ink-soft)",
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** Commits on release, not on every pixel: dragging a slider must not fire
 *  fifty saves, fifty writes to disk and fifty config.changed events. */
function Slider({
  value,
  min,
  max,
  step,
  onCommit,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onCommit: (value: number) => void;
}) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <input
      type="range"
      className="no-drag"
      min={min}
      max={max}
      step={step}
      value={local}
      onChange={(event) => setLocal(Number(event.target.value))}
      onPointerUp={() => local !== value && onCommit(local)}
      onKeyUp={() => local !== value && onCommit(local)}
      style={{ width: 148, accentColor: "var(--accent)" }}
    />
  );
}

const ACCENT_NAMES: Record<string, string> = {
  cyan: "Cyjan",
  blue: "Niebieski",
  violet: "Fiolet",
  teal: "Morski",
  green: "Zielony",
  amber: "Bursztyn",
  rose: "Róż",
};

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
  // Defaults until the first frame arrives, so the controls are never blank.
  const look = engine?.appearance ?? DEFAULT_APPEARANCE;
  // A provider GARIS does not build at all has no measurement; "brak klucza" is
  // the honest reading of that, not "unknown".
  const providerNamed = (name: string): Provider =>
    engine?.models.providers.find((provider) => provider.name === name) ?? {
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
    };

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
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 6 }}>
          <RecheckButton />
        </div>
        <Glass style={{ padding: "6px 16px", borderRadius: "var(--radius-card)" }}>
          {PROVIDERS.map(([secretName, label, engineName]) => (
            <Row
              key={secretName}
              label={label}
              /* Not "klucz w sejfie": a rejected key is also in the vault, and
                 saying so was how an invalid key looked healthy for an hour. */
              status={providerNamed(engineName)}
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

      <Section title="Wygląd">
        <Glass style={{ padding: "6px 16px", borderRadius: "var(--radius-card)" }}>
          <Row label="Motyw" hint="„Systemowy” idzie za pulpitem, także po zmianie o zmierzchu">
            <Choice
              value={look.theme}
              options={[
                ["system", "Systemowy"],
                ["dark", "Ciemny"],
                ["light", "Jasny"],
              ]}
              onChange={(value) => void patch({ "appearance.theme": value })}
            />
          </Row>
          <Row label="Kolor akcentu">
            <div style={{ display: "flex", gap: 7 }}>
              {Object.entries(ACCENTS).map(([name, hsl]) => (
                <button
                  key={name}
                  aria-label={ACCENT_NAMES[name] ?? name}
                  aria-pressed={look.accent === name}
                  onClick={() => void patch({ "appearance.accent": name })}
                  className="no-drag"
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: "var(--radius-pill)",
                    background: `hsl(${hsl})`,
                    border:
                      look.accent === name
                        ? "2px solid var(--ink)"
                        : "1px solid var(--glass-stroke)",
                    cursor: "pointer",
                  }}
                />
              ))}
            </div>
          </Row>
          <Row label="Intensywność szkła" hint="Do zera, jeśli wolisz płaskie tło">
            <Slider
              value={look.glass}
              min={0}
              max={1}
              step={0.1}
              onCommit={(value) => void patch({ "appearance.glass": value })}
            />
          </Row>
          <Row label="Wielkość tekstu">
            <Slider
              value={look.text_scale}
              min={0.9}
              max={1.4}
              step={0.05}
              onCommit={(value) => void patch({ "appearance.text_scale": value })}
            />
          </Row>
          <Row label="Gęstość" hint="Kompaktowo mieści więcej na małym ekranie">
            <Choice
              value={look.density}
              options={[
                ["comfortable", "Swobodnie"],
                ["compact", "Kompaktowo"],
              ]}
              onChange={(value) => void patch({ "appearance.density": value })}
            />
          </Row>
          <Row label="Animacje" hint="„Systemowe” respektuje ustawienie dostępności">
            <Choice
              value={look.animation}
              options={[
                ["system", "Systemowe"],
                ["full", "Pełne"],
                ["off", "Wyłączone"],
              ]}
              onChange={(value) => void patch({ "appearance.animation": value })}
            />
          </Row>
          <Row label="Wysoki kontrast" hint="Rezygnuje z przezroczystości na rzecz czytelności">
            <Toggle
              on={look.high_contrast}
              onChange={(value) => void patch({ "appearance.high_contrast": value })}
            />
          </Row>
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
