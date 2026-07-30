/** The conversation: what was asked, what came back. */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";
import type { ChatEntry } from "../lib/store";
import { messageVariants, spring } from "../lib/motion";
import { useStore } from "../lib/store";
import { Empty, Glass, Typewriter, relativeTime } from "./ui";

function Bubble({ entry, isLast }: { entry: ChatEntry; isLast: boolean }) {
  const mine = entry.role === "user";
  const tone =
    entry.kind === "question"
      ? "38 92% 62%"
      : entry.kind === "report"
        ? "var(--state-ok)"
        : "var(--state-idle)";

  return (
    <motion.div
      layout
      variants={messageVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
      style={{
        display: "flex",
        justifyContent: mine ? "flex-end" : "flex-start",
        marginBottom: 10,
      }}
    >
      <Glass
        interactive={false}
        style={{
          maxWidth: "76%",
          padding: "11px 15px",
          borderRadius: mine
            ? "var(--radius-card) var(--radius-card) 6px var(--radius-card)"
            : "var(--radius-card) var(--radius-card) var(--radius-card) 6px",
          background: mine ? "var(--accent-soft)" : undefined,
          borderColor: mine
            ? "hsl(var(--state-working) / 0.34)"
            : entry.kind === "question"
              ? `hsl(${tone} / 0.34)`
              : undefined,
        }}
      >
        <div className="selectable" style={{ lineHeight: 1.55 }}>
          {/* Only the newest reply types itself in. Replaying the whole history
              on every render would be noise, not personality. */}
          {!mine && isLast ? <Typewriter text={entry.text} /> : entry.text}
        </div>
        <div className="tiny faint" style={{ marginTop: 5 }}>
          {relativeTime(entry.at)}
        </div>
      </Glass>
    </motion.div>
  );
}

export function Conversation({ compact = false }: { compact?: boolean }) {
  const chat = useStore((s) => s.chat);
  const agentState = useStore((s) => s.agentState);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.length, agentState]);

  const entries = compact ? chat.slice(-4) : chat;

  if (entries.length === 0 && !compact) {
    return (
      <Empty
        icon="💬"
        title="Jeszcze nie rozmawialiśmy."
        hint="Powiedz, jaki rezultat chcesz uzyskać — resztą zajmę się sam."
      />
    );
  }

  return (
    <div className="scroll" style={{ display: "grid", alignContent: "start" }}>
      <motion.div layout>
        <AnimatePresence initial={false}>
          {entries.map((entry, index) => (
            <Bubble
              key={entry.id}
              entry={entry}
              isLast={index === entries.length - 1}
            />
          ))}
        </AnimatePresence>
      </motion.div>

      {/* "GARIS is thinking" is three dots that actually move — a static
          placeholder makes a working agent look frozen. */}
      <AnimatePresence>
        {(agentState === "thinking" || agentState === "working") && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={spring}
            style={{ display: "flex", gap: 5, padding: "6px 4px" }}
          >
            {[0, 1, 2].map((index) => (
              <motion.span
                key={index}
                animate={{ y: [0, -5, 0], opacity: [0.35, 1, 0.35] }}
                transition={{
                  duration: 1.1,
                  repeat: Infinity,
                  delay: index * 0.16,
                  ease: "easeInOut",
                }}
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: "var(--ink-soft)",
                }}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      <div ref={endRef} />
    </div>
  );
}
