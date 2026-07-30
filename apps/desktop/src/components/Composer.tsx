/**
 * Where the user says what they want.
 *
 * One field, no mode switches, no tool picker, no model dropdown. Typing and
 * speaking are the same input — the button holds the microphone, the field takes
 * text, and both submit the same thing: an outcome.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { base, quick, spring } from "../lib/motion";
import { useStore } from "../lib/store";

const SUGGESTIONS = [
  "sprawdź, co zajmuje miejsce na dysku",
  "pokaż, jakie procesy zjadają pamięć",
  "przygotuj notatkę z tego projektu",
  "sprawdź stan serwera",
];

export function Composer() {
  const send = useStore((s) => s.send);
  const agentState = useStore((s) => s.agentState);
  const setLevel = useStore((s) => s.setLevel);
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const busy = agentState === "thinking" || agentState === "working";

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Ctrl+Alt+G focuses the field from anywhere in the window.
      if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "g") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const submit = async () => {
    const goal = text.trim();
    if (!goal) return;
    setText("");
    await send(goal);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  };

  /**
   * Push-to-talk. Stage 2 wires the button and the level meter; the actual
   * speech pipeline lands in stage 3, so holding it currently only proves the
   * orb reacts to real microphone amplitude.
   */
  const startListening = async () => {
    setListening(true);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);

      let running = true;
      const tick = () => {
        if (!running) return;
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (const sample of data) {
          const centred = (sample - 128) / 128;
          sum += centred * centred;
        }
        setLevel(Math.min(1, Math.sqrt(sum / data.length) * 4));
        requestAnimationFrame(tick);
      };
      tick();

      const stop = () => {
        running = false;
        setLevel(0);
        setListening(false);
        stream.getTracks().forEach((track) => track.stop());
        void context.close();
        window.removeEventListener("pointerup", stop);
      };
      window.addEventListener("pointerup", stop);
    } catch {
      setListening(false);
      setLevel(0);
    }
  };

  return (
    <motion.div layout transition={spring} style={{ display: "grid", gap: 10 }}>
      <AnimatePresence>
        {!text && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6, height: 0 }}
            transition={base}
            style={{ display: "flex", gap: 8, flexWrap: "wrap" }}
          >
            {SUGGESTIONS.map((suggestion, index) => (
              <motion.button
                key={suggestion}
                className="btn btn--quiet tiny"
                onClick={() => setText(suggestion)}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ ...base, delay: 0.04 * index }}
                whileHover={{ y: -2 }}
                whileTap={{ scale: 0.97 }}
                style={{ borderRadius: "var(--radius-pill)" }}
              >
                {suggestion}
              </motion.button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      <motion.div
        layout
        className="glass glass--interactive"
        animate={{
          borderColor: listening
            ? "hsl(var(--state-listening) / 0.55)"
            : "var(--glass-stroke)",
          boxShadow: listening
            ? "0 0 0 4px hsl(var(--state-listening) / 0.14), var(--shadow-panel)"
            : "var(--shadow-panel)",
        }}
        transition={base}
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 10,
          padding: 10,
          borderRadius: "var(--radius-panel)",
        }}
      >
        <textarea
          ref={inputRef}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Powiedz, co ma być zrobione…"
          className="selectable"
          style={{
            flex: 1,
            resize: "none",
            border: "none",
            outline: "none",
            background: "transparent",
            color: "var(--ink)",
            padding: "9px 8px",
            maxHeight: 140,
            fontSize: 15,
          }}
        />

        <motion.button
          className="btn no-drag"
          onPointerDown={startListening}
          aria-label="Przytrzymaj, żeby mówić"
          title="Przytrzymaj, żeby mówić"
          animate={
            listening
              ? { scale: [1, 1.06, 1], transition: { duration: 1.2, repeat: Infinity } }
              : { scale: 1 }
          }
          whileTap={{ scale: 0.94 }}
          transition={quick}
          style={{
            borderRadius: "50%",
            width: 40,
            height: 40,
            padding: 0,
            background: listening ? "hsl(var(--state-listening) / 0.24)" : undefined,
          }}
        >
          🎙
        </motion.button>

        <motion.button
          className="btn btn--primary no-drag"
          onClick={submit}
          disabled={!text.trim()}
          whileHover={text.trim() ? { y: -1 } : undefined}
          whileTap={text.trim() ? { scale: 0.97 } : undefined}
          transition={quick}
          style={{ borderRadius: "var(--radius-control)", height: 40 }}
        >
          {busy ? "Dodaj" : "Zrób to"}
        </motion.button>
      </motion.div>
    </motion.div>
  );
}
