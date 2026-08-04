/**
 * First run: a greeting, not a form.
 *
 * One question per screen, asked the way a person would ask it, each one
 * skippable. GARIS adapts to the user — so the first thing it does is learn how
 * to address them, not demand configuration.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useRef, useState } from "react";
import { SPRING, variants } from "../lib/motion";
import { useModalKeys } from "./Dialog";
import { useStore } from "../lib/store";
import { Presence } from "./Presence";
import { Glass, Pill } from "./ui";

interface Step {
  id: string;
  line: string;
  hint?: string;
  kind: "text" | "choice" | "secret" | "done";
  placeholder?: string;
  choices?: [string, string][];
  configKey?: string;
  secretName?: string;
}

/**
 * First run asks four things, and none of them is a wake word.
 *
 * "Powiedz „Garis", a Cię usłyszę" was the third screen, and it was not true:
 * there is no speech path in the engine, Settings → Głos says so plainly, and
 * the microphone button is disabled for that reason. Promising it during the
 * first thirty seconds and denying it in Settings is the kind of contradiction
 * this release exists to remove.
 */
const STEPS: Step[] = [
  {
    id: "name",
    line: "Cześć. Jestem GARIS.",
    hint: "Jak mam się do Ciebie zwracać?",
    kind: "text",
    placeholder: "np. Michał albo szefie",
    configKey: "identity.address_as",
  },
  {
    id: "persona",
    line: "Jak mam mówić?",
    hint: "Zawsze możesz to zmienić.",
    kind: "choice",
    configKey: "persona.preset",
    choices: [
      ["assistant", "Rzeczowo"],
      ["kolega", "Luźno"],
      ["butler", "Formalnie"],
      ["cichy", "Jak najmniej"],
    ],
  },
  {
    id: "key",
    line: "Żebym mógł myśleć, potrzebuję dostępu do modelu.",
    hint: "Klucz trafi do zaszyfrowanego sejfu, nie do pliku konfiguracyjnego. Możesz to zrobić później.",
    kind: "secret",
    placeholder: "klucz OpenAI, Claude albo Gemini",
    secretName: "openai_api_key",
  },
  {
    id: "done",
    line: "To wszystko. Powiedz, co mam zrobić.",
    hint: "Nie musisz tłumaczyć jak — od tego jestem ja.",
    kind: "done",
  },
];

export function Onboarding({ onFinish }: { onFinish: () => void }) {
  const api = useStore((s) => s.api);
  const refresh = useStore((s) => s.refresh);
  const [index, setIndex] = useState(0);
  const [value, setValue] = useState("");
  const panel = useRef<HTMLDivElement | null>(null);
  const step = STEPS[index];

  const advance = async (submitted?: string) => {
    const answer = (submitted ?? value).trim();

    if (answer && api) {
      try {
        if (step.kind === "secret" && step.secretName) {
          await api.storeSecret(step.secretName, answer, "Klucz dostawcy modeli");
        } else if (step.configKey) {
          await api.patchConfig({ [step.configKey]: answer });
        }
      } catch {
        // A failed answer must never trap someone on the first screen.
      }
    }

    setValue("");
    if (index + 1 >= STEPS.length) {
      await api?.patchConfig({ "identity.onboarded": true });
      await refresh();
      onFinish();
      return;
    }
    setIndex(index + 1);
  };

  const finish = async () => {
    await api?.patchConfig({ "identity.onboarded": true });
    await refresh();
    onFinish();
  };

  // Modal in the strict sense: there is nothing else to do in the window yet, so
  // the keyboard stays inside. Escape is deliberately not a way out — leaving is
  // "Pomiń wszystko", a decision that says what it does, not a keystroke that
  // silently marks setup finished.
  useModalKeys({ active: true, panel });

  return (
    <motion.div
      className="onboarding"
      variants={variants("scrim")}
      initial="hidden"
      animate="visible"
      exit="exit"
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Pierwsze uruchomienie"
        style={{ display: "grid", justifyItems: "center", gap: 26 }}
      >
        <motion.div
          initial={{ scale: 0.7, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ ...SPRING.panel, delay: 0.1 }}
        >
          <Presence state={index === STEPS.length - 1 ? "completed" : "idle"} size={16} />
        </motion.div>

        <Glass
          raised
          style={{
            width: "min(520px, calc(100vw - 48px))",
            padding: 26,
            display: "grid",
            gap: 16,
          }}
        >
          {/* Each question replaces the last in place, so the screen reads as one
              continuous conversation rather than a wizard with pages. */}
          <AnimatePresence mode="wait">
            <motion.div
              key={step.id}
              variants={variants("panel")}
              initial="hidden"
              animate="visible"
              exit="exit"
              style={{ display: "grid", gap: 14 }}
            >
              <div style={{ display: "grid", gap: 6 }}>
                <h1 style={{ fontSize: 21 }}>{step.line}</h1>
                {step.hint && <p className="soft">{step.hint}</p>}
              </div>

              {step.kind === "text" && (
                <input
                  className="field"
                  autoFocus
                  value={value}
                  placeholder={step.placeholder}
                  onChange={(event) => setValue(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && void advance()}
                />
              )}

              {step.kind === "secret" && (
                <input
                  className="field"
                  autoFocus
                  type="password"
                  value={value}
                  placeholder={step.placeholder}
                  onChange={(event) => setValue(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && void advance()}
                />
              )}

              {step.kind === "choice" && (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {step.choices?.map(([key, label]) => (
                    <Pill key={key} onClick={() => void advance(key)}>
                      {label}
                    </Pill>
                  ))}
                </div>
              )}
            </motion.div>
          </AnimatePresence>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {/* Progress is a set of dots that fill, not "3/12" — a form counts
                steps, a conversation does not. */}
            <div style={{ display: "flex", gap: 6, flex: 1 }}>
              {STEPS.map((item, position) => (
                <motion.span
                  key={item.id}
                  animate={{
                    width: position === index ? 22 : 6,
                    opacity: position <= index ? 1 : 0.32,
                  }}
                  transition={SPRING.control}
                  style={{
                    height: 6,
                    borderRadius: "var(--radius-pill)",
                    background:
                      position <= index ? "hsl(var(--state-working))" : "var(--ink-faint)",
                    display: "block",
                  }}
                />
              ))}
            </div>

            {step.kind !== "done" ? (
              <>
                <button className="btn btn--quiet tiny" onClick={() => void advance("")}>
                  Pomiń
                </button>
                <button className="btn btn--primary" onClick={() => void advance()}>
                  Dalej
                </button>
              </>
            ) : (
              <button className="btn btn--primary" onClick={() => void finish()}>
                Zaczynamy
              </button>
            )}
          </div>
        </Glass>

        <button className="btn btn--quiet tiny" onClick={() => void finish()}>
          Pomiń wszystko
        </button>
      </div>
    </motion.div>
  );
}
