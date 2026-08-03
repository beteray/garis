/**
 * Everything GARIS has been asked to do.
 *
 * Grouped by what the user has to do about it, not by when it was created: a
 * list sorted purely by time buries the one blocked task under fifty finished
 * ones, and the blocked one is the only reason to open this screen.
 *
 * The card is `TaskCard`, shared with the conversation, so a task looks and
 * behaves the same wherever it is seen — and "otwórz szczegóły" means the same
 * thing in both places.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import type { Task, TaskState } from "../lib/api";
import { stagger } from "../lib/motion";
import { useStore } from "../lib/store";
import { TaskCard } from "./TaskCard";
import { Empty, Section } from "./ui";

const FILTERS: [string, string, (task: Task) => boolean][] = [
  ["all", "Wszystkie", () => true],
  ["needs", "Wymaga Ciebie", (task) => task.state === "blocked"],
  ["active", "W toku", (task) => task.state === "running" || task.state === "pending"],
  ["done", "Zrobione", (task) => task.state === "finished"],
  ["failed", "Nieudane", (task) => task.state === "failed" || task.state === "stopped"],
];

/** Blocked first: it is the only group where nothing happens until somebody acts. */
const ORDER: Record<TaskState, number> = {
  blocked: 0,
  running: 1,
  pending: 2,
  failed: 3,
  finished: 4,
  stopped: 5,
};

export function TasksView() {
  const tasks = useStore((s) => s.tasks);
  const refresh = useStore((s) => s.refresh);
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const match = FILTERS.find(([id]) => id === filter)?.[2] ?? (() => true);
  const shown = [...tasks]
    .filter(match)
    .sort((a, b) => ORDER[a.state] - ORDER[b.state] || b.updated_at - a.updated_at);

  const counts = Object.fromEntries(
    FILTERS.map(([id, , predicate]) => [id, tasks.filter(predicate).length]),
  ) as Record<string, number>;

  return (
    <motion.div variants={stagger()} initial="hidden" animate="visible" className="screen">
      <Section title="Zadania">
        <div className="filters" role="tablist" aria-label="Filtr zadań">
          {FILTERS.map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={filter === id}
              className="btn btn--quiet tiny"
              data-active={filter === id || undefined}
              onClick={() => setFilter(id)}
            >
              {label}
              {counts[id] > 0 && <span className="nav-item__badge tiny">{counts[id]}</span>}
            </button>
          ))}
        </div>

        {shown.length === 0 ? (
          <Empty
            icon="◷"
            title={filter === "all" ? "Jeszcze nic nie robiłem." : "Nic w tej grupie."}
            hint={filter === "all" ? "Powiedz na Starcie, co ma być zrobione." : undefined}
          />
        ) : (
          <div className="task-list">
            <AnimatePresence initial={false}>
              {shown.map((task) => (
                <TaskCard key={task.id} taskId={task.id} />
              ))}
            </AnimatePresence>
          </div>
        )}
      </Section>
    </motion.div>
  );
}
