/**
 * What GARIS is doing, as one small thing you can glance at.
 *
 * This replaces a 340-pixel WebGL orb that ran a fragment shader forever. It
 * was the largest object on the daily surface and it carried one bit of
 * information; the room it occupied was room the conversation needed. What
 * replaces it is deliberately small, says its state in a word as well as a
 * colour, and costs nothing when nothing is happening.
 *
 * Rules it keeps:
 *  - never colour alone — a mark and a word, both;
 *  - animation only while the state is genuinely busy, so an idle GARIS is a
 *    still GARIS and the GPU is free;
 *  - nothing at all when the window is hidden, reduced motion is on, or the
 *    user turned the indicator off;
 *  - it scales with the layout instead of being a fixed ornament.
 */

import { motion } from "framer-motion";
import type { RuntimeState } from "../lib/runtimeState";
import { PRESENTATION } from "../lib/runtimeState";
import { prefersReducedMotion } from "../lib/motion";
import { useDocumentVisible } from "../lib/useDocumentVisible";

export interface PresenceProps {
  state: RuntimeState;
  /** rem-ish: the dot scales with text, so 175% Windows scaling grows it too. */
  size?: number;
  /** Drop the word and the live region — only where the state is already
   *  written out next to it, so a reader is not told the same thing twice. */
  compact?: boolean;
}

export function Presence({ state, size = 10, compact = false }: PresenceProps) {
  const visible = useDocumentVisible();
  const look = PRESENTATION[state] ?? PRESENTATION.idle;
  // Three independent reasons to hold still, and any one of them is enough.
  const animate = look.busy && visible && !prefersReducedMotion();

  return (
    <span
      className="presence"
      // One live region for the whole application's status. Announced when it
      // changes, never interrupting — "polite" is the difference between a
      // status line and a shout. A compact indicator is decoration beside text
      // that already says it, so it announces nothing.
      role={compact ? undefined : "status"}
      aria-live={compact ? undefined : "polite"}
      aria-hidden={compact || undefined}
      data-state={state}
    >
      <motion.span
        className="presence__dot"
        aria-hidden
        style={{ width: size, height: size, background: look.tone }}
        animate={animate ? { opacity: [0.45, 1, 0.45], scale: [1, 1.18, 1] } : { opacity: 1, scale: 1 }}
        transition={
          animate
            ? { duration: 1.6, repeat: Infinity, ease: "easeInOut" }
            : { duration: 0.2 }
        }
      />
      {/* The mark carries the state without colour and without motion — it is
          what survives a screenshot, a monochrome display, and a person who
          cannot separate these hues. */}
      <span className="presence__mark" aria-hidden style={{ color: look.tone }}>
        {look.mark}
      </span>
      {!compact && <span className="presence__label tiny">{look.label}</span>}
    </span>
  );
}

/** The same state as a full-width banner, for the top of Home. */
export function PresenceBanner({
  state,
  detail,
  action,
}: {
  state: RuntimeState;
  detail?: string;
  action?: React.ReactNode;
}) {
  const look = PRESENTATION[state] ?? PRESENTATION.idle;
  return (
    <div className="presence-banner" data-state={state} role="status" aria-live="polite">
      <Presence state={state} size={12} compact />
      <div className="presence-banner__text">
        <strong>{look.label}</strong>
        {detail && <span className="tiny soft">{detail}</span>}
      </div>
      {action}
    </div>
  );
}
