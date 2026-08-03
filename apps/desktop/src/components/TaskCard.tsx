/**
 * A task, as the person who asked for it needs to see it.
 *
 * One component for both places it appears: embedded in the conversation where
 * the work was started, and in the Tasks screen. Embedding it is what lets
 * "otwórz szczegóły" not mean "leave the thread" — details expand in place.
 *
 * The rule this file exists to enforce: **never say only "czekam na Ciebie".**
 * The engine persists the actual question in `TaskRecord.error` when a task is
 * blocked, and the approval broker knows which operation is waiting. Both are
 * shown verbatim. A blocked task with neither is a bug in the engine, and the
 * card says that rather than shrugging.
 *
 * What is deliberately absent: a percentage and an ETA. The engine knows which
 * steps exist and which finished, and nothing more — a plan can grow a step
 * mid-run. "4 z 6 kroków" is a fact; "67%" is a promise nobody can keep.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import type { Task, TaskState } from "../lib/api";
import { outcomeOf } from "../lib/chat";
import { SPRING } from "../lib/motion";
import { useStore } from "../lib/store";
import { relativeTime } from "./ui";

/** Six engine states, and what a person is told about each.
 *
 *  `core/src/garis/tasks/models.py` has exactly these six. There is no PARTIAL
 *  and no "effect uncertain" — where the brief asks for those, the strongest
 *  honest reading comes from the verifier's report, below. */
const STATE: Record<TaskState, { tone: string; mark: string; label: string }> = {
  pending: { tone: "var(--state-busy)", mark: "◌", label: "W kolejce" },
  running: { tone: "var(--state-working)", mark: "◑", label: "Robię" },
  blocked: { tone: "var(--state-warn)", mark: "!", label: "Czeka na Ciebie" },
  finished: { tone: "var(--state-ok)", mark: "✓", label: "Gotowe" },
  failed: { tone: "var(--state-error)", mark: "✕", label: "Nie udało się" },
  stopped: { tone: "var(--ink-faint)", mark: "■", label: "Zatrzymane" },
};

/** What a finished task actually established. */
const OUTCOME: Record<string, { tone: string; label: string; note: string }> = {
  verified: {
    tone: "var(--state-ok)",
    label: "Zrobione i sprawdzone",
    note: "Sprawdziłem wynik względem tego, o co prosiłeś.",
  },
  unverified: {
    tone: "var(--state-ok)",
    label: "Zrobione, niesprawdzone",
    note: "Kroki się powiodły, ale nie potwierdziłem wyniku.",
  },
  partial: {
    tone: "var(--state-warn)",
    label: "Zrobione częściowo",
    note: "Część się udała — poniżej czego brakuje.",
  },
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="task__row">
      <span className="tiny faint task__row-label">{label}</span>
      <div className="task__row-value">{children}</div>
    </div>
  );
}

/** The one thing standing between this task and finishing. */
function Blocking({ task }: { task: Task }) {
  const approvals = useStore((s) => s.approvals);
  const resolve = useStore((s) => s.resolveApproval);
  const waiting = approvals.filter((approval) => approval.task_id === task.id);
  const question = task.question || task.error;

  if (waiting.length > 0) {
    const [approval] = waiting;
    return (
      <div className="task__blocking" data-kind="approval">
        <strong className="tiny">Czekam na zgodę</strong>
        <p className="message__text">{approval.prompt}</p>
        <div className="message__effects">
          {approval.effects.map((effect) => (
            <span key={effect} className="tiny effect-chip">
              {effect}
            </span>
          ))}
        </div>
        <div className="message__actions">
          <button className="btn tiny" onClick={() => void resolve(approval.id, true)}>
            Zgoda
          </button>
          <button
            className="btn btn--quiet tiny"
            onClick={() => void resolve(approval.id, false)}
          >
            Nie
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="task__blocking" data-kind="question">
      <strong className="tiny">Czekam na odpowiedź</strong>
      <p className="message__text">
        {question || (
          // Stating the bug rather than hiding it: docs/STATE_MACHINES.md calls
          // a blocked task without a question a program error, not a state.
          <span className="soft">
            Zadanie czeka, ale nie zapisało pytania — to błąd silnika, nie Twój.
          </span>
        )}
      </p>
    </div>
  );
}

function Steps({ task }: { task: Task }) {
  const steps = task.steps ?? [];
  if (!steps.length) return null;
  const done = steps.filter((step) => step.state === "done");
  const current = steps.find((step) => step.state === "running");
  const next = steps.find((step) => step.state === "pending");

  return (
    <>
      {current && <Row label="Teraz">{current.tool}</Row>}
      {!current && next && <Row label="Następnie">{next.tool}</Row>}
      <Row label="Kroki">
        {/* Counted, never estimated. */}
        {done.length} z {steps.length} zrobionych
        {steps.some((step) => step.state === "failed") && (
          <span className="tiny" style={{ color: "hsl(var(--state-error))" }}>
            {" "}· {steps.filter((s) => s.state === "failed").length} nieudanych
          </span>
        )}
      </Row>
    </>
  );
}

export function TaskCard({
  taskId,
  embedded = false,
  defaultOpen = false,
}: {
  taskId: string;
  embedded?: boolean;
  defaultOpen?: boolean;
}) {
  const task = useStore((s) => s.tasks.find((candidate) => candidate.id === taskId));
  const progress = useStore((s) => s.progress);
  const api = useStore((s) => s.api);
  const stopTask = useStore((s) => s.stopTask);
  const runAsTask = useStore((s) => s.runAsTask);
  const developer = useStore((s) => s.engine?.dev.developer_mode ?? false);
  const [open, setOpen] = useState(defaultOpen);
  const [full, setFull] = useState<Task | null>(null);

  // Steps and the plan are not in the list payload; fetch them only when the
  // details are actually opened, not for every card on screen.
  useEffect(() => {
    if (!open || !api || !task) return;
    let alive = true;
    void api
      .task(task.id)
      .then((detail) => alive && setFull(detail))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [open, api, task, task?.state, task?.updated_at]);

  if (!task) {
    return (
      <div className="task task--missing glass glass--card">
        <span className="tiny faint">Zadanie zniknęło z listy.</span>
      </div>
    );
  }

  const state = STATE[task.state];
  const shown = full ?? task;
  const lines = progress.filter((line) => line.taskId === task.id).slice(-4);
  const outcome = task.state === "finished" ? OUTCOME[outcomeOf(task.report)] : null;

  return (
    <motion.article
      layout
      className="task glass glass--card"
      data-state={task.state}
      data-embedded={embedded || undefined}
      transition={SPRING.panel}
    >
      <header className="task__head">
        <span aria-hidden className="task__mark" style={{ color: state.tone }}>
          {state.mark}
        </span>
        <div className="task__title">
          {/* The goal in the user's own words is the title. Anything else is a
              label GARIS invented for something the user already named. */}
          <strong className="task__goal">{task.goal}</strong>
          <span className="tiny faint">
            <span style={{ color: state.tone }}>{state.label}</span>
            {" · "}
            {relativeTime(task.updated_at)}
            {task.target && task.target !== "local" && ` · ${task.target}`}
          </span>
        </div>
        <button
          className="btn btn--quiet tiny"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Zwiń" : "Szczegóły"}
        </button>
      </header>

      {task.state === "blocked" && <Blocking task={task} />}

      {outcome && (
        <div className="task__outcome" style={{ borderColor: outcome.tone }}>
          <strong className="tiny" style={{ color: outcome.tone }}>
            {outcome.label}
          </strong>
          <p className="message__text">{task.report?.short}</p>
          <span className="tiny faint">{outcome.note}</span>
          {task.report?.problems?.map((problem) => (
            <span key={problem} className="tiny" style={{ color: "hsl(var(--state-warn))" }}>
              — {problem}
            </span>
          ))}
        </div>
      )}

      {task.state === "failed" && (
        <div className="task__outcome" style={{ borderColor: "var(--state-error)" }}>
          <p className="message__text">{task.error || task.report?.short}</p>
        </div>
      )}

      {task.state === "running" && lines.length > 0 && (
        <div className="task__progress">
          {lines.map((line, index) => (
            <span key={`${line.at}-${index}`} className="tiny faint">
              {line.text}
            </span>
          ))}
        </div>
      )}

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="task__details"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={SPRING.panel}
          >
            <Row label="Cel">{task.goal}</Row>
            {task.criteria.length > 0 && (
              <Row label="Warunki">{task.criteria.join(" · ")}</Row>
            )}
            <Row label="Gdzie">{task.target === "local" ? "Ten komputer" : task.target}</Row>
            <Steps task={shown} />
            {shown.plan?.summary && <Row label="Plan">{shown.plan.summary}</Row>}
            {task.report?.details && <Row label="Szczegóły">{task.report.details}</Row>}
            {developer && (
              <Row label="Id">
                <code className="mono tiny selectable">{task.id}</code>
              </Row>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="task__actions">
        {(task.state === "running" || task.state === "pending") && (
          <button className="btn btn--quiet tiny" onClick={() => void stopTask(task.id)}>
            Zatrzymaj
          </button>
        )}
        {(task.state === "failed" || task.state === "stopped") && (
          <button className="btn btn--quiet tiny" onClick={() => void runAsTask(task.goal)}>
            Spróbuj jeszcze raz
          </button>
        )}
      </div>
    </motion.article>
  );
}

export { STATE as TASK_STATE };
