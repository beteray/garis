/**
 * Where the user says what they want.
 *
 * One field, no mode switches, no tool picker, no model dropdown. What is typed
 * goes to `/api/say` and the engine decides whether it is work — the window
 * does not get a vote, because the window guessing is exactly what turned
 * "cześć" into a permanent blocked task.
 *
 * There is a second, explicit path: "Wykonaj jako zadanie" posts to
 * `/api/tasks`, which always creates one. It is a button the user presses on
 * purpose, never a heuristic.
 *
 * The microphone button is disabled and says so. It used to open the real
 * microphone and feed a level meter into an orb — a permission prompt in
 * exchange for a decoration, with no speech pipeline behind it. Voice is not
 * implemented; pretending otherwise with a live capture is worse than a
 * greyed-out control.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { SPRING, TWEEN, tactile } from "../lib/motion";
import { useStore } from "../lib/store";

const SUGGESTIONS = [
  "sprawdź, co zajmuje miejsce na dysku",
  "pokaż, jakie procesy zjadają pamięć",
  "sprawdź stan serwera",
];

export function Composer({ showSuggestions = true }: { showSuggestions?: boolean }) {
  const send = useStore((s) => s.send);
  const runAsTask = useStore((s) => s.runAsTask);
  const sending = useStore((s) => s.sending);
  const draft = useStore((s) => s.composerDraft);
  const setDraft = useStore((s) => s.setComposerDraft);
  const [text, setText] = useState("");
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // "Popraw" on a sent message puts it back here, focused and selected, so the
  // fix is one edit rather than a retype.
  useEffect(() => {
    if (!draft) return;
    setText(draft);
    setDraft("");
    const field = inputRef.current;
    field?.focus();
    field?.setSelectionRange(draft.length, draft.length);
  }, [draft, setDraft]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "g") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = async (explicit = false) => {
    const goal = text.trim();
    if (!goal) return;
    setText("");
    await (explicit ? runAsTask(goal) : send(goal));
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit(event.ctrlKey);
    }
  };

  return (
    <motion.div layout transition={SPRING.panel} className="composer">
      <AnimatePresence>
        {showSuggestions && !text && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6, height: 0 }}
            transition={TWEEN.content}
            className="composer__suggestions"
          >
            {SUGGESTIONS.map((suggestion, index) => (
              <motion.button
                key={suggestion}
                className="btn btn--quiet tiny"
                onClick={() => {
                  setText(suggestion);
                  inputRef.current?.focus();
                }}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...TWEEN.content, delay: 0.04 * index }}
                {...tactile()}
                style={{ borderRadius: "var(--radius-pill)" }}
              >
                {suggestion}
              </motion.button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="composer__field glass">
        <label className="sr-only" htmlFor="composer-input">
          Powiedz, co ma być zrobione
        </label>
        <textarea
          id="composer-input"
          ref={inputRef}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Powiedz, co ma być zrobione…"
          className="selectable composer__input"
        />

        <button
          className="btn no-drag composer__mic"
          disabled
          aria-label="Mówienie — jeszcze niedostępne"
          title="Mówienie jeszcze nie działa. Wpisz polecenie."
        >
          <span aria-hidden>🎙</span>
        </button>

        <button
          className="btn no-drag"
          onClick={() => void submit(true)}
          disabled={!text.trim() || sending}
          title="Utwórz zadanie bez pytania, czy to rozmowa (Ctrl+Enter)"
        >
          Zadanie
        </button>

        <motion.button
          className="btn btn--primary no-drag"
          onClick={() => void submit(false)}
          disabled={!text.trim() || sending}
          {...tactile({ disabled: !text.trim() || sending })}
        >
          {sending ? "Wysyłam…" : "Wyślij"}
        </motion.button>
      </div>
      <span className="tiny faint composer__hint">
        Enter wysyła · Shift+Enter nowa linia · Ctrl+Enter od razu jako zadanie
      </span>
    </motion.div>
  );
}
