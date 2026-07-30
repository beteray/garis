/**
 * First run: a greeting, not a form.
 *
 * One question per screen, asked the way a person would ask it, each one
 * skippable. GARIS adapts to the user — so the first thing it does is learn how
 * to address them, not demand configuration.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { EASE, base, spring } from "../lib/motion";
import { useStore } from "../lib/store";
import { Orb } from "./Orb";
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
    id: "wake",
    line: "Powiedz „Garis”, a Cię usłyszę.",
    hint: "Możesz zmienić to słowo na dowolne inne.",
    kind: "text",
    placeholder: "garis",
    configKey: "voice.wake_word",
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

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.4, ease: EASE }}
      style={{
        position: "fixed",
        inset: 0,
        display: "grid",
        placeItems: "center",
        zIndex: 60,
        background: "rgba(6, 8, 12, 0.42)",
        backdropFilter: "blur(24px)",
      }}
    >
      <div style={{ display: "grid", justifyItems: "center", gap: 26 }}>
        <motion.div
          initial={{ scale: 0.7, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ ...spring, delay: 0.1 }}
        >
          <Orb state={index === STEPS.length - 1 ? "done" : "idle"} size={200} />
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
              initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -14, filter: "blur(6px)" }}
              transition={base}
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
                  transition={spring}
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
