/**
 * Work in flight and work finished.
 *
 * Tasks enter, move and leave with animation because the list is the only place
 * where "GARIS is doing several things at once" becomes visible. A row that pops
 * into existence reads as a refresh; a row that slides in reads as something
 * starting.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import type { Task } from "../lib/api";
import { base, listItemVariants, quick, spring, staggerContainer } from "../lib/motion";
import { activeTasks, finishedTasks, useStore } from "../lib/store";
import { Empty, Glass, relativeTime } from "./ui";

const STATE_TONE: Record<string, string> = {
  pending: "var(--state-idle)",
  running: "var(--state-working)",
  blocked: "38 92% 62%",
  finished: "var(--state-ok)",
  failed: "var(--state-error)",
  stopped: "0 0% 60%",
};

const STATE_LABEL: Record<string, string> = {
  pending: "w kolejce",
  running: "w toku",
  blocked: "czeka na Ciebie",
  finished: "gotowe",
  failed: "nie udało się",
  stopped: "zatrzymane",
};

export function TaskRow({ task }: { task: Task }) {
  const [open, setOpen] = useState(false);
  const stopTask = useStore((s) => s.stopTask);
  const progress = useStore((s) => s.progress);
  const tone = STATE_TONE[task.state] ?? "var(--state-idle)";
  const live = progress.filter((line) => line.taskId === task.id).slice(-1)[0];
  const running = task.state === "running" || task.state === "pending";

  return (
    <motion.div
      layout
      variants={listItemVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
    >
      <Glass style={{ padding: 14, borderRadius: "var(--radius-card)" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          {/* The status dot pulses only while there is genuinely something
              happening — a still dot means a still task. */}
          <motion.span
            aria-hidden
            animate={
              running
                ? { scale: [1, 1.35, 1], opacity: [0.75, 1, 0.75] }
                : { scale: 1, opacity: 1 }
            }
            transition={{ duration: 1.6, repeat: running ? Infinity : 0, ease: "easeInOut" }}
            style={{
              width: 9,
              height: 9,
              borderRadius: "50%",
              marginTop: 6,
              flexShrink: 0,
              background: `hsl(${tone})`,
              boxShadow: `0 0 12px hsl(${tone} / 0.7)`,
            }}
          />

          <button
            onClick={() => setOpen((value) => !value)}
            className="btn btn--quiet"
            style={{
              flex: 1,
              justifyContent: "flex-start",
              textAlign: "left",
              padding: 0,
              background: "transparent",
            }}
            aria-expanded={open}
          >
            <div style={{ display: "grid", gap: 3, width: "100%" }}>
              <div style={{ fontWeight: 550 }}>{task.goal}</div>
              <div className="tiny soft" style={{ display: "flex", gap: 8 }}>
                <span style={{ color: `hsl(${tone})` }}>
                  {STATE_LABEL[task.state] ?? task.state}
                </span>
                <span>·</span>
                <span>{relativeTime(task.updated_at)}</span>
                {task.target !== "local" && (
                  <>
                    <span>·</span>
                    <span>{task.target}</span>
                  </>
                )}
              </div>
            </div>
          </button>

          {running && (
            <motion.button
              className="btn btn--quiet tiny"
              onClick={() => void stopTask(task.id)}
              whileHover={{ y: -1 }}
              whileTap={{ scale: 0.96 }}
              transition={quick}
            >
              Zatrzymaj
            </motion.button>
          )}
        </div>

        {/* The live progress line replaces itself in place: one line, always the
            most recent, never a growing log in the user's face. */}
        <AnimatePresence mode="wait">
          {running && live && (
            <motion.div
              key={live.text}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={base}
              className="tiny faint"
              style={{ marginTop: 8, paddingLeft: 21 }}
            >
              {live.text}
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={spring}
              style={{ overflow: "hidden" }}
            >
              <div style={{ paddingTop: 12, display: "grid", gap: 8 }}>
                {task.report?.short && (
                  <p className="selectable">{task.report.short}</p>
                )}
                {task.question && (
                  <p className="selectable" style={{ color: `hsl(${STATE_TONE.blocked})` }}>
                    {task.question}
                  </p>
                )}
                {task.report?.details && (
                  <pre
                    className="mono soft selectable"
                    style={{
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      maxHeight: 220,
                      overflow: "auto",
                    }}
                  >
                    {task.report.details}
                  </pre>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Glass>
    </motion.div>
  );
}

export function TasksView() {
  const tasks = useStore((s) => s.tasks);
  const active = activeTasks(tasks);
  const done = finishedTasks(tasks);

  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="visible"
      style={{ display: "grid", gap: 22 }}
    >
      <section style={{ display: "grid", gap: 12 }}>
        <h2>Aktywne</h2>
        {active.length === 0 ? (
          <Empty icon="🌙" title="Nic teraz nie robię." hint="Powiedz, co mam zrobić." />
        ) : (
          <motion.div layout>
            <AnimatePresence initial={false}>
              {active.map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </section>

      {done.length > 0 && (
        <section style={{ display: "grid", gap: 12 }}>
          <h2 className="soft">Zakończone</h2>
          <motion.div layout>
            <AnimatePresence initial={false}>
              {done.slice(0, 25).map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
            </AnimatePresence>
          </motion.div>
        </section>
      )}
    </motion.div>
  );
}
