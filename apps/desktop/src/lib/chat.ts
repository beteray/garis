/**
 * The kinds of thing that appear in the conversation, and what each one means.
 *
 * A single bubble style for everything is what made the old surface unreadable:
 * "cześć" and "I need administrator rights to continue" looked identical, so
 * the eye had to read every line to find the one that mattered. These are the
 * distinct things GARIS can put on screen, each earning its own treatment.
 *
 * Every kind here maps to something the engine actually produces. `/api/say`
 * returns `kind` and `intent`; the event stream carries `task.*`, `approval.*`
 * and `notice`. Nothing in this list is a category the window made up.
 */

export type Entry =
  /** The person typed something. */
  | "user"
  /** Conversation: a greeting, a thank-you, an acknowledgement. */
  | "chat"
  /** A computed answer — arithmetic, a count of tools, what GARIS is doing. */
  | "answer"
  /** A goal that became work. Renders as an embedded task card. */
  | "task"
  /** GARIS needs an answer before it can continue. */
  | "clarification"
  /** GARIS needs a yes before it does something with an effect. */
  | "approval"
  /** A step happened. Collapsed by default — progress is not conversation. */
  | "progress"
  /** Finished, and the verifier agreed. */
  | "verified"
  /** Finished, but nothing checked it. Not the same claim. */
  | "unverified"
  /** Some of it worked. */
  | "partial"
  /** It did not work. */
  | "failure"
  /** The system talking about itself: a key saved, a provider gone bad. */
  | "notice";

export interface Presentation {
  /** Border and mark colour. Never the only signal. */
  tone: string;
  mark: string;
  /** Screen-reader prefix and visible eyebrow, so the kind is readable text. */
  label: string;
  /** Which glass material carries it. */
  material: "card" | "approval" | "plain";
  /** Whether it deserves to interrupt the eye. */
  emphasis: "quiet" | "normal" | "loud";
}

export const ENTRY: Record<Entry, Presentation> = {
  user: { tone: "var(--accent)", mark: "", label: "Ty", material: "plain", emphasis: "normal" },
  chat: { tone: "var(--state-idle)", mark: "", label: "GARIS", material: "plain", emphasis: "quiet" },
  answer: { tone: "var(--state-idle)", mark: "≡", label: "Odpowiedź", material: "card", emphasis: "normal" },
  task: { tone: "var(--state-working)", mark: "◷", label: "Zadanie", material: "card", emphasis: "normal" },
  clarification: { tone: "var(--state-warn)", mark: "?", label: "Pytanie", material: "approval", emphasis: "loud" },
  approval: { tone: "var(--state-warn)", mark: "!", label: "Zgoda", material: "approval", emphasis: "loud" },
  progress: { tone: "var(--state-working)", mark: "·", label: "Postęp", material: "plain", emphasis: "quiet" },
  verified: { tone: "var(--state-ok)", mark: "✓", label: "Zrobione i sprawdzone", material: "card", emphasis: "normal" },
  unverified: { tone: "var(--state-ok)", mark: "◇", label: "Zrobione, niesprawdzone", material: "card", emphasis: "normal" },
  partial: { tone: "var(--state-warn)", mark: "◐", label: "Zrobione częściowo", material: "card", emphasis: "normal" },
  failure: { tone: "var(--state-error)", mark: "✕", label: "Nie udało się", material: "card", emphasis: "loud" },
  notice: { tone: "var(--state-idle)", mark: "·", label: "Komunikat", material: "plain", emphasis: "quiet" },
};

/**
 * How a finished task should be described.
 *
 * The engine reports `report.verified`, so "done" and "done and checked" are
 * genuinely different claims and are shown as such. `partial` is derived from
 * the verifier's own `problems` list on an otherwise successful task — the
 * task store has no PARTIAL state (`core/src/garis/tasks/models.py` has six),
 * so this is the strongest honest reading available. "Effect uncertain" has no
 * representation at all and is not rendered; see docs/STATE_MACHINES.md.
 */
export function outcomeOf(report: {
  verified?: boolean;
  problems?: string[];
} | undefined): Entry {
  if (!report) return "unverified";
  if (report.problems?.length) return "partial";
  return report.verified ? "verified" : "unverified";
}
