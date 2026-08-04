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
import { ACCENTS, DEFAULT_APPEARANCE } from "../lib/appearance";
import { ModelsSettings } from "./Models";
import { SPRING, TWEEN, tactile, variants } from "../lib/motion";
import { useStore } from "../lib/store";
import { Glass, Pill, Section } from "./ui";

const PERSONAS = [
  ["assistant", "Asystent", "Rzeczowy, konkretny."],
  ["kolega", "Kolega", "Luźniej, z humorem."],
  ["butler", "Kamerdyner", "Formalnie i uprzejmie."],
  ["cichy", "Cichy", "Minimum słów."],
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
      <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
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
        transition={SPRING.control}
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
        <motion.button
          key={id}
          role="radio"
          aria-checked={value === id}
          onClick={() => onChange(id)}
          {...tactile({ lift: false })}
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
        </motion.button>
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


/** The categories, in the order a person needs them. Voice is last and says
 *  plainly that it does not work — a section that lies about being available is
 *  worse than a section that is honest about being empty. */
const CATEGORIES = [
  ["general", "Ogólne"],
  ["appearance", "Wygląd"],
  ["models", "Modele"],
  ["privacy", "Pamięć i prywatność"],
  ["notifications", "Powiadomienia"],
  ["security", "Bezpieczeństwo"],
  ["advanced", "Zaawansowane"],
  ["voice", "Głos"],
] as const;

type Category = (typeof CATEGORIES)[number][0];

export function SettingsView() {
  const engine = useStore((s) => s.engine);
  const api = useStore((s) => s.api);
  const refresh = useStore((s) => s.refresh);
  const [category, setCategory] = useState<Category>("general");
  const [name, setName] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    if (!engine) return;
    setName(engine.identity.address_as || engine.identity.name);
  }, [engine]);

  const patch = async (changes: Record<string, unknown>) => {
    if (!api) return;
    setProblem("");
    try {
      await api.patchConfig(changes);
      setSaved(Object.keys(changes)[0] ?? null);
      setTimeout(() => setSaved(null), 1600);
      await refresh();
    } catch (error) {
      // A refused setting must say so. Silently reverting is how a settings
      // screen teaches people that it does not work.
      setProblem(error instanceof Error ? error.message : "Nie udało się zapisać.");
    }
  };

  const look = engine?.appearance ?? DEFAULT_APPEARANCE;
  const quiet = engine?.notifications;

  return (
    <div className="settings">
      <nav className="settings__tabs" aria-label="Kategorie ustawień">
        {CATEGORIES.map(([id, label]) => (
          <button
            key={id}
            className="btn btn--quiet tiny"
            aria-current={category === id ? "true" : undefined}
            data-active={category === id || undefined}
            onClick={() => setCategory(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {/* The new category replaces the old one outright — no cross-fade, and
          deliberately no exit. Two bodies on screen at once would put the
          previous category's controls in the tab order and in the accessibility
          tree while they fade, and a switch that is still readable behind its
          replacement reads as a bug rather than as motion. The arrival is
          animated; the departure is immediate. */}
      <motion.div
        key={category}
        variants={variants("panel")}
        initial="hidden"
        animate="visible"
        className="settings__body scroll"
      >
        {category === "general" && (
          <>
            <Section title="Ty">
              <Glass className="glass--card rows">
                <Row label="Jak mam się do Ciebie zwracać" hint="Np. imieniem albo „szefie”">
                  <input
                    className="field"
                    style={{ width: 200 }}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    onBlur={() => void patch({ "identity.address_as": name })}
                    aria-label="Jak mam się do Ciebie zwracać"
                  />
                </Row>
                <Row label="Język" hint="W tym języku mówię i piszę">
                  <span className="soft">{engine?.identity.language ?? "pl"}</span>
                </Row>
                <Row label="Uruchamiaj przy starcie systemu">
                  <Toggle
                    on={engine?.autostart ?? true}
                    onChange={(value) => void patch({ autostart: value })}
                  />
                </Row>
              </Glass>
            </Section>

            <Section title="Osobowość">
              <p className="tiny soft">
                Zmienia ton, humor i długość wypowiedzi. Nigdy nie zmienia zasad
                bezpieczeństwa ani sposobu wykonywania zadań.
              </p>
              <div className="filters">
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
          </>
        )}

        {category === "appearance" && (
          <Section title="Wygląd">
            <Glass className="glass--card rows">
              <Row label="Motyw" hint="„Systemowy” idzie za pulpitem, także po zmianie o zmierzchu">
                <Choice
                  value={look.theme}
                  options={[["system", "Systemowy"], ["dark", "Ciemny"], ["light", "Jasny"]]}
                  onChange={(value) => void patch({ "appearance.theme": value })}
                />
              </Row>
              <Row label="Kolor akcentu">
                <div className="swatches">
                  {Object.entries(ACCENTS).map(([id, hsl]) => (
                    <button
                      key={id}
                      aria-label={ACCENT_NAMES[id] ?? id}
                      aria-pressed={look.accent === id}
                      onClick={() => void patch({ "appearance.accent": id })}
                      className="swatch no-drag"
                      data-active={look.accent === id || undefined}
                      style={{ background: `hsl(${hsl})` }}
                    />
                  ))}
                  <label className="swatch swatch--custom" title="Własny kolor">
                    <input
                      type="color"
                      aria-label="Własny kolor akcentu"
                      value={look.accent.startsWith("#") ? look.accent : "#22c1e8"}
                      onChange={(event) =>
                        void patch({ "appearance.accent": event.target.value })
                      }
                    />
                  </label>
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
              <Row label="Wielkość tekstu" hint="Składa się ze skalowaniem Windows">
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
                  options={[["comfortable", "Swobodnie"], ["compact", "Kompaktowo"]]}
                  onChange={(value) => void patch({ "appearance.density": value })}
                />
              </Row>
              <Row label="Nawigacja" hint="„Automatycznie” zwija się, gdy okno jest wąskie">
                <Choice
                  value={look.navigation}
                  options={[["auto", "Automatycznie"], ["labels", "Z opisami"], ["rail", "Ikony"]]}
                  onChange={(value) => void patch({ "appearance.navigation": value })}
                />
              </Row>
              <Row label="Animacje" hint="„Systemowe” respektuje ustawienie dostępności">
                <Choice
                  value={look.animation}
                  options={[["system", "Systemowe"], ["full", "Pełne"], ["off", "Wyłączone"]]}
                  onChange={(value) => void patch({ "appearance.animation": value })}
                />
              </Row>
              <Row label="Wskaźnik stanu" hint="Kropka przy nazwie — jak bardzo ma się ruszać">
                <Choice
                  value={look.orb}
                  options={[["full", "Pełny"], ["simple", "Prosty"], ["off", "Bez ruchu"]]}
                  onChange={(value) => void patch({ "appearance.orb": value })}
                />
              </Row>
              <Row label="Wysoki kontrast" hint="Rezygnuje z przezroczystości na rzecz czytelności">
                <Toggle
                  on={look.high_contrast}
                  onChange={(value) => void patch({ "appearance.high_contrast": value })}
                />
              </Row>
              <Row
                label="Dźwięki"
                hint="Silnik jeszcze ich nie odtwarza — ustawienie czeka na tę część"
              >
                <Toggle
                  on={look.sounds}
                  onChange={(value) => void patch({ "appearance.sounds": value })}
                />
              </Row>
            </Glass>
          </Section>
        )}

        {category === "models" && <ModelsSettings />}

        {category === "privacy" && (
          <Section title="Pamięć i prywatność">
            <Glass className="glass--card rows">
              <Row label="Prywatność modeli" hint="Gdzie wolno wysyłać treść zadań">
                <Choice
                  value={engine?.models.privacy ?? "balanced"}
                  options={[
                    ["local_only", "Tylko lokalnie"],
                    ["prefer_local", "Najpierw lokalnie"],
                    ["balanced", "Zrównoważona"],
                    ["quality_first", "Jakość"],
                  ]}
                  onChange={(value) => void patch({ "models.privacy": value })}
                />
              </Row>
              <Row label="Wolno korzystać z chmury">
                <Toggle
                  on={engine?.models.allow_cloud ?? true}
                  onChange={(value) => void patch({ "models.allow_cloud": value })}
                />
              </Row>
            </Glass>
            <p className="tiny soft">
              Co pamiętam i co trzymam w sejfie, obejrzysz i skasujesz w zakładce
              Pamięć. Sekrety nigdy nie trafiają do modelu.
            </p>
          </Section>
        )}

        {category === "notifications" && (
          <Section title="Powiadomienia">
            <Glass className="glass--card rows">
              <Row label="Godziny ciszy">
                <Toggle
                  on={quiet?.quiet_hours.enabled ?? true}
                  onChange={(value) => void patch({ "notifications.quiet_hours.enabled": value })}
                />
              </Row>
              <Row label="Od">
                <input
                  className="field"
                  type="time"
                  value={quiet?.quiet_hours.start ?? "23:00"}
                  onChange={(event) =>
                    void patch({ "notifications.quiet_hours.start": event.target.value })
                  }
                  aria-label="Cisza od"
                />
              </Row>
              <Row label="Do">
                <input
                  className="field"
                  type="time"
                  value={quiet?.quiet_hours.end ?? "08:00"}
                  onChange={(event) =>
                    void patch({ "notifications.quiet_hours.end": event.target.value })
                  }
                  aria-label="Cisza do"
                />
              </Row>
              <Row label="Cisza podczas grania" hint="Ustawienie działa; wykrywanie gry jeszcze nie">
                <Toggle
                  on={quiet?.suppress_while_gaming ?? true}
                  onChange={(value) =>
                    void patch({ "notifications.suppress_while_gaming": value })
                  }
                />
              </Row>
              <Row label="Najwyżej na godzinę">
                <Slider
                  value={quiet?.max_per_hour ?? 6}
                  min={1}
                  max={20}
                  step={1}
                  onCommit={(value) => void patch({ "notifications.max_per_hour": value })}
                />
              </Row>
            </Glass>
          </Section>
        )}

        {category === "security" && (
          <Section title="Bezpieczeństwo">
            <p className="tiny soft">
              Te reguły obowiązują niezależnie od osobowości i od tego, o co
              poprosisz w rozmowie.
            </p>
            <Glass className="glass--card rows">
              <Row label="Mogę prosić o uprawnienia administratora">
                <Toggle
                  on={engine?.capabilities.policy.allow_admin_elevation ?? true}
                  onChange={(value) => void patch({ "autonomy.allow_admin_elevation": value })}
                />
              </Row>
              <Row label="Pytam przed" hint="Skutki, których nie zrobię bez Twojej zgody">
                <div className="message__effects">
                  {(engine?.capabilities.policy.confirm_effects ?? []).map((effect) => (
                    <span key={effect} className="tiny effect-chip effect-chip--danger">
                      {effect}
                    </span>
                  ))}
                </div>
              </Row>
            </Glass>
          </Section>
        )}

        {category === "advanced" && (
          <Section title="Zaawansowane">
            <Glass className="glass--card rows">
              <Row label="Szczegółowe raporty" hint="Pokazuj kroki, nie tylko wynik">
                <Toggle
                  on={engine?.dev.verbose ?? false}
                  onChange={(value) => void patch({ "dev.verbose": value })}
                />
              </Row>
              <Row
                label="Tryb developerski"
                hint="Odblokowuje Diagnostykę: narzędzia, dziennik, surowe dane modeli"
              >
                <Toggle
                  on={engine?.dev.developer_mode ?? false}
                  onChange={(value) => void patch({ "dev.developer_mode": value })}
                />
              </Row>
              <Row label="Równolegle zadań">
                <Slider
                  value={engine?.tasks.max_parallel ?? 4}
                  min={1}
                  max={12}
                  step={1}
                  onCommit={(value) => void patch({ "tasks.max_parallel": value })}
                />
              </Row>
            </Glass>
          </Section>
        )}

        {category === "voice" && (
          <Section title="Głos">
            <Glass className="glass--card rows unavailable">
              <p>
                <strong>Jeszcze nie działa.</strong>
              </p>
              <p className="tiny soft">
                Rozpoznawanie mowy i mówienie nie są zaimplementowane — w silniku
                nie ma jeszcze tej ścieżki. Przycisk mikrofonu w polu poleceń jest
                z tego powodu wyłączony, a nie „chwilowo niedostępny”. Ustawienia
                głosu pojawią się tutaj, kiedy będzie czym sterować.
              </p>
            </Glass>
          </Section>
        )}

      </motion.div>

        <div className="settings__status" aria-live="polite">
          {problem ? (
            <span className="tiny" style={{ color: "hsl(var(--state-error))" }}>
              {problem}
            </span>
          ) : (
            <motion.span
              initial={false}
              animate={{ opacity: saved ? 1 : 0 }}
              transition={TWEEN.content}
              className="tiny"
              style={{ color: "hsl(var(--state-ok))" }}
            >
              {saved ? "Zapisane." : ""}
            </motion.span>
          )}
        </div>
    </div>
  );
}

export const settingsTransition = TWEEN.control;
