/**
 * One entry in the conversation, rendered as what it actually is.
 *
 * The old surface put everything in the same rounded rectangle, so a greeting
 * and "I need administrator rights" were the same shape and the same weight.
 * Here each kind earns its treatment: an approval is a decision card with
 * buttons, a result states whether anything checked it, progress is a quiet
 * line rather than a message, and ordinary chat stays out of the way.
 *
 * Actions are on the entry, not hidden behind a hover menu: copy always, retry
 * and edit where re-sending means something, and technical detail only in
 * developer mode.
 */

import { motion } from "framer-motion";
import { useState } from "react";
import type { ChatEntry } from "../lib/store";
import { ENTRY } from "../lib/chat";
import { useStore } from "../lib/store";
import { messageVariants } from "../lib/motion";
import { relativeTime } from "./ui";
import { TaskCard } from "./TaskCard";

function Actions({ entry }: { entry: ChatEntry }) {
  const send = useStore((s) => s.send);
  const setComposerDraft = useStore((s) => s.setComposerDraft);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(entry.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* a clipboard the OS refused is not an error worth a dialog */
    }
  };

  return (
    <div className="message__actions">
      {entry.text && (
        <button className="btn btn--quiet tiny" onClick={() => void copy()}>
          {copied ? "Skopiowane" : "Kopiuj"}
        </button>
      )}
      {entry.kind === "user" && (
        <>
          <button className="btn btn--quiet tiny" onClick={() => void send(entry.text)}>
            Wyślij ponownie
          </button>
          <button
            className="btn btn--quiet tiny"
            onClick={() => setComposerDraft(entry.text)}
          >
            Popraw
          </button>
        </>
      )}
      {entry.kind === "failure" && entry.taskId && <RetryTask taskId={entry.taskId} />}
    </div>
  );
}

function RetryTask({ taskId }: { taskId: string }) {
  const tasks = useStore((s) => s.tasks);
  const runAsTask = useStore((s) => s.runAsTask);
  const goal = tasks.find((task) => task.id === taskId)?.goal;
  if (!goal) return null;
  return (
    <button className="btn btn--quiet tiny" onClick={() => void runAsTask(goal)}>
      Spróbuj jeszcze raz
    </button>
  );
}

function ApprovalCard({ entry }: { entry: ChatEntry }) {
  const approvals = useStore((s) => s.approvals);
  const resolve = useStore((s) => s.resolveApproval);
  const developer = useStore((s) => s.engine?.dev.developer_mode ?? false);
  const approval = approvals.find((candidate) => candidate.id === entry.approvalId);

  return (
    <div className="message__body">
      <p className="message__text">{entry.text}</p>
      {approval && (
        <>
          <p className="tiny soft">{approval.prompt}</p>
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
          {developer && (
            <code className="mono tiny faint selectable">{approval.tool}</code>
          )}
        </>
      )}
      {!approval && (
        // The approval was resolved elsewhere — from the tray, the CLI, another
        // window. Saying so beats leaving dead buttons on screen.
        <p className="tiny faint">Rozstrzygnięte.</p>
      )}
    </div>
  );
}

export function Message({ entry }: { entry: ChatEntry }) {
  const developer = useStore((s) => s.engine?.dev.developer_mode ?? false);
  const look = ENTRY[entry.kind] ?? ENTRY.notice;
  const mine = entry.kind === "user";

  // A task entry is a card, not a sentence: the conversation is where work
  // starts, and the work needs to stay visible without leaving the thread.
  if (entry.kind === "task" && entry.taskId) {
    return (
      <motion.div variants={messageVariants} initial="hidden" animate="visible" exit="exit">
        <TaskCard taskId={entry.taskId} embedded />
      </motion.div>
    );
  }

  if (entry.kind === "progress") {
    return (
      <motion.div
        className="message message--progress"
        variants={messageVariants}
        initial="hidden"
        animate="visible"
        exit="exit"
      >
        <span aria-hidden className="message__mark" style={{ color: look.tone }}>
          {look.mark}
        </span>
        <span className="tiny faint">{entry.text}</span>
      </motion.div>
    );
  }

  return (
    <motion.article
      className="message"
      data-kind={entry.kind}
      data-emphasis={look.emphasis}
      data-mine={mine || undefined}
      variants={messageVariants}
      initial="hidden"
      animate="visible"
      exit="exit"
    >
      <header className="message__head">
        {look.mark && (
          <span aria-hidden className="message__mark" style={{ color: look.tone }}>
            {look.mark}
          </span>
        )}
        {/* The kind is a word, so it survives monochrome, screenshots and a
            reader that cannot see the border colour. */}
        <span className="message__kind tiny" style={{ color: look.tone }}>
          {look.label}
        </span>
        <span className="tiny faint">{relativeTime(entry.at)}</span>
        {developer && entry.intent && (
          <code className="mono tiny faint">{entry.intent}</code>
        )}
      </header>

      {entry.kind === "approval" ? (
        <ApprovalCard entry={entry} />
      ) : (
        <div className="message__body">
          <p className="message__text selectable">{entry.text}</p>
        </div>
      )}

      <Actions entry={entry} />
    </motion.article>
  );
}
