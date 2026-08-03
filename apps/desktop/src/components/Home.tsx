/**
 * The daily surface. Everything a person opens GARIS to find out.
 *
 * It answers four questions in the order they get asked:
 *   1. What is GARIS doing?          — the status line, from measured state
 *   2. Does it need anything from me? — pinned above the thread, never scrolled
 *      away, because a question you have to hunt for is a question that stalls
 *   3. What happened recently?        — the conversation itself
 *   4. What can I ask now?            — the composer, always reachable
 *
 * Conversation is not a separate route. The events that matter — a question, an
 * approval, a report — are already delivered here by `store.handleEvent`, so a
 * separate tab would mean the main surface is the one you navigate away from
 * Home to reach.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import { Composer } from "./Composer";
import { Message } from "./Message";
import { PresenceBanner } from "./Presence";
import { TaskCard } from "./TaskCard";
import type { RuntimeState } from "../lib/runtimeState";
import { useStore } from "../lib/store";
import { Empty } from "./ui";

/** How many entries are rendered at once.
 *
 *  The store keeps 300; a thread that long is thousands of DOM nodes and every
 *  event re-renders it. Older entries stay in memory and come back when the
 *  user asks for them — incremental rather than virtualised, because the rows
 *  are different heights and a windowing library would fight the layout
 *  animation. */
const PAGE = 40;

export function Home({ state }: { state: RuntimeState }) {
  const chat = useStore((s) => s.chat);
  const tasks = useStore((s) => s.tasks);
  const approvals = useStore((s) => s.approvals);
  const engine = useStore((s) => s.engine);
  const setView = useStore((s) => s.setView);
  const [shown, setShown] = useState(PAGE);
  const endRef = useRef<HTMLDivElement | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);

  const visible = useMemo(() => chat.slice(-shown), [chat, shown]);

  // Follow the conversation, but only when the user is already at the bottom:
  // yanking the view down while somebody is reading is how a chat log becomes
  // unusable during a long task.
  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) return;
    const nearBottom =
      thread.scrollHeight - thread.scrollTop - thread.clientHeight < 160;
    if (nearBottom) endRef.current?.scrollIntoView({ block: "end" });
  }, [chat.length]);

  // What genuinely needs the person, from the engine's own lists — never a
  // count the window derived from optimism.
  const needsYou = tasks.filter((task) => task.state === "blocked");
  const running = tasks.filter(
    (task) => task.state === "running" || task.state === "pending",
  );
  // A provider the engine measured as unusable. `unknown` is not one of these:
  // not having checked yet is not the same as having found a problem.
  const broken = (engine?.models.providers ?? []).find(
    (provider) => !provider.available && provider.status !== "unknown",
  );

  const detail =
    running.length > 0
      ? `${running.length} ${running.length === 1 ? "zadanie" : "zadania"} w toku`
      : !engine
        ? "Czekam na silnik."
        : broken
          ? // The engine's own words for what it found, so the banner cannot
            // drift from what the Connections screen says.
            `${broken.name}: ${broken.reason}`
          : undefined;

  return (
    <div className="home">
      <PresenceBanner
        state={state}
        detail={detail}
        action={
          // "Something needs attention" with nowhere to go is a dead end. This
          // appears only when the engine actually measured a failure.
          broken && running.length === 0 ? (
            <button className="btn btn--quiet tiny" onClick={() => setView("settings")}>
              Napraw
            </button>
          ) : undefined
        }
      />

      {/* Pinned, not in the thread: an approval that scrolls out of sight is an
          approval that never gets answered. */}
      <AnimatePresence initial={false}>
        {needsYou.length > 0 && (
          <motion.section
            className="home__needs"
            aria-label="Wymaga Twojej decyzji"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
          >
            {needsYou.slice(0, 3).map((task) => (
              <TaskCard key={task.id} taskId={task.id} embedded />
            ))}
            {needsYou.length > 3 && (
              <span className="tiny faint">
                …i jeszcze {needsYou.length - 3}. Zobacz Zadania.
              </span>
            )}
          </motion.section>
        )}
      </AnimatePresence>

      <div className="home__thread scroll" ref={threadRef}>
        {chat.length === 0 ? (
          <Empty
            icon="◎"
            title="Jeszcze nic tu nie ma."
            hint="Albo po prostu się przywitaj — nie zrobię z tego zadania."
          />
        ) : (
          <>
            {shown < chat.length && (
              <button
                className="btn btn--quiet tiny home__more"
                onClick={() => setShown(shown + PAGE)}
              >
                Pokaż wcześniejsze ({chat.length - shown})
              </button>
            )}
            <AnimatePresence initial={false}>
              {visible.map((entry) => (
                <Message key={entry.id} entry={entry} />
              ))}
            </AnimatePresence>
          </>
        )}
        <div ref={endRef} />
      </div>

      <Composer showSuggestions={chat.length === 0 && approvals.length === 0} />
    </div>
  );
}
