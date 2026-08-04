/**
 * One modal implementation, for everything modal.
 *
 * Before this, every overlay in GARIS reimplemented the same four things —
 * Escape, focus trap, focus restoration, backdrop — and each got a slightly
 * different subset right. The overlay navigation trapped focus; onboarding did
 * not, so a keyboard user could tab into the window behind a full-screen
 * dialog. Deleting a memory asked nothing at all before doing it.
 *
 * The rules encoded here, in order of how badly they hurt when missed:
 *
 *   1. **Focus goes in, and comes back out where it came from.** Anything else
 *      strands the person somewhere they cannot see.
 *   2. **Focus is trapped only while genuinely modal.** A non-modal panel that
 *      swallows Tab is a bug wearing a bug's clothes, so `modal={false}` gets
 *      Escape and a scrim but no trap.
 *   3. **The exit animates and then the dialog is gone.** Framer keeps the node
 *      mounted while it plays; `pointer-events: none` goes on during exit so a
 *      fading scrim cannot eat a click meant for what is behind it.
 *   4. **It grows from where it came from.** A dialog opened by a button in the
 *      corner has no business zooming out of the middle of the screen; the
 *      opener's position sets `transform-origin`, so the movement points back at
 *      the thing that caused it.
 *
 * All motion comes from `lib/motion.ts`. There are no durations in this file.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode, RefObject } from "react";
import { variants } from "../lib/motion";

const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Keep the keyboard inside `panel` while `active`, and hand focus back to
 * whatever had it when this started.
 *
 * Exported because the overlay navigation is a dialog too, and two copies of a
 * focus trap is one copy too many.
 */
export function useModalKeys({
  active,
  modal = true,
  panel,
  onClose,
}: {
  active: boolean;
  modal?: boolean;
  panel: RefObject<HTMLElement>;
  onClose?: () => void;
}) {
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    if (!active) return;
    opener.current = document.activeElement;

    // The first control, not the panel itself: a screen reader announces the
    // dialog from its label, and landing on a button says what can be done.
    const first = panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Stopped here so one Escape closes one thing: a confirmation inside a
        // dialog must not take the dialog down with it.
        event.stopPropagation();
        onClose?.();
        return;
      }
      if (!modal || event.key !== "Tab" || !panel.current) return;
      const focusable = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!focusable.length) return;
      const head = focusable[0];
      const tail = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === head) {
        event.preventDefault();
        tail.focus();
      } else if (!event.shiftKey && document.activeElement === tail) {
        event.preventDefault();
        head.focus();
      }
    };

    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      // Only if the opener is still on the page — a control that removed itself
      // (the "Zapomnij" button of a memory now forgotten) cannot take focus, and
      // insisting would drop focus onto <body> instead.
      const back = opener.current as HTMLElement | null;
      if (back && back.isConnected) back.focus?.();
    };
  }, [active, modal, panel, onClose]);
}

/** Where a dialog should appear to come from: the control that opened it. */
export function useOrigin() {
  const [origin, setOrigin] = useState<string | undefined>();
  const remember = useCallback((event?: { currentTarget: Element }) => {
    const rect = event?.currentTarget.getBoundingClientRect();
    if (!rect) return setOrigin(undefined);
    // Percentages of the viewport, so the origin survives a resize between the
    // click and the first frame.
    const x = ((rect.left + rect.width / 2) / window.innerWidth) * 100;
    const y = ((rect.top + rect.height / 2) / window.innerHeight) * 100;
    setOrigin(`${x.toFixed(1)}% ${y.toFixed(1)}%`);
  }, []);
  return { origin, remember };
}

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Shown under the title. Plain words: this is where the consequence goes. */
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  /** Traps focus. False for a panel that is only visually on top. */
  modal?: boolean;
  /** `transform-origin` from `useOrigin`, so it grows out of its opener. */
  origin?: string;
  /** Widened for content that needs it; the default suits a question. */
  width?: number;
  className?: string;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  modal = true,
  origin,
  width = 460,
  className = "",
}: DialogProps) {
  const panel = useRef<HTMLDivElement | null>(null);
  const id = useId();

  useModalKeys({ active: open, modal, panel, onClose });

  // Rendered into <body>: inside the tree it would inherit the panel's clipping
  // and its stacking context, and a dialog that a parent can crop is not a
  // dialog. `document` is absent while tests import the module, never while one
  // renders.
  if (typeof document === "undefined") return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="dialog-layer" data-open="true">
          <motion.div
            className="dialog__scrim"
            variants={variants("scrim")}
            initial="hidden"
            animate="visible"
            exit="exit"
            onClick={onClose}
            aria-hidden
          />
          <motion.div
            ref={panel}
            role="dialog"
            aria-modal={modal}
            aria-labelledby={`${id}-title`}
            aria-describedby={description ? `${id}-desc` : undefined}
            className={`glass glass--raised dialog ${className}`}
            style={{ width: `min(${width}px, calc(100vw - 40px))`, transformOrigin: origin }}
            variants={variants("dialog")}
            initial="hidden"
            animate="visible"
            exit="exit"
          >
            <h2 id={`${id}-title`} className="dialog__title">
              {title}
            </h2>
            {description && (
              <p id={`${id}-desc`} className="dialog__description soft">
                {description}
              </p>
            )}
            {children}
            {footer && <div className="dialog__footer">{footer}</div>}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

export interface ConfirmProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  /** The word on the button that does the thing. Never "OK". */
  confirmLabel: string;
  cancelLabel?: string;
  /** Irreversible: the button carries the warning colour. */
  danger?: boolean;
  origin?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * The question asked before something cannot be taken back.
 *
 * Cancel holds focus, not confirm. Someone who hits Enter out of habit should
 * end up where they were, not one keystroke past a deletion.
 */
export function Confirm({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Anuluj",
  danger = false,
  origin,
  onConfirm,
  onCancel,
}: ConfirmProps) {
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      description={description}
      origin={origin}
      width={420}
      className="dialog--confirm"
      footer={
        <>
          <button className="btn btn--quiet" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            className={danger ? "btn btn--danger" : "btn btn--primary"}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </>
      }
    />
  );
}
