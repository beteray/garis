/**
 * The moment GARIS asks.
 *
 * Rare by design, so when it happens it must be unmissable without being alarming:
 * the card slides in over everything, states the consequence in the user's words,
 * shows exactly which effects are involved, and animates the answer so the person
 * sees that their decision landed.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import type { Approval } from "../lib/api";
import { AMBIENT, LOOP, SPRING, TWEEN, tactile, variants } from "../lib/motion";
import { useStore } from "../lib/store";
import { EffectBadge, Glass } from "./ui";

const EFFECT_LABEL: Record<string, string> = {
  payment: "płatność",
  publish: "publikacja",
  send_message: "wysłanie wiadomości",
  credentials: "dane logowania",
  delete_permanent: "trwałe usunięcie",
  install: "instalacja",
  elevate: "uprawnienia administratora",
  system_config: "zmiana systemu",
  network: "sieć",
  exec: "uruchomienie",
  write: "zapis",
  read: "odczyt",
};

function Card({ approval }: { approval: Approval }) {
  const resolve = useStore((s) => s.resolveApproval);
  const [answered, setAnswered] = useState<null | boolean>(null);

  const answer = async (approved: boolean) => {
    setAnswered(approved);
    // Let the confirmation animation play before the card leaves — an instant
    // disappearance leaves the user unsure whether the tap registered.
    setTimeout(() => void resolve(approval.id, approved), 420);
  };

  return (
    <motion.div
      layout
      variants={variants("attention")}
      initial="hidden"
      animate="visible"
      exit="exit"
      style={{ width: "min(560px, calc(100vw - 48px))" }}
    >
      <Glass
        raised
        style={{
          padding: 20,
          display: "grid",
          gap: 14,
          borderColor: "hsl(38 92% 62% / 0.36)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <motion.span
            aria-hidden
            animate={{ scale: [1, 1.12, 1] }}
            transition={{ duration: AMBIENT.attention, ...LOOP }}
            style={{ fontSize: 20 }}
          >
            🔐
          </motion.span>
          <h3 style={{ flex: 1 }}>Potrzebuję Twojej zgody</h3>
        </div>

        <p className="selectable" style={{ fontSize: 15.5, lineHeight: 1.55 }}>
          {approval.prompt}
        </p>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {approval.effects.map((effect) => (
            <EffectBadge
              key={effect}
              effect={effect}
              label={EFFECT_LABEL[effect] ?? effect}
            />
          ))}
        </div>

        <AnimatePresence mode="wait">
          {answered === null ? (
            <motion.div
              key="buttons"
              exit={{ opacity: 0, y: -6 }}
              transition={TWEEN.control}
              style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}
            >
              <motion.button
                className="btn"
                onClick={() => void answer(false)}
                {...tactile()}
              >
                Nie
              </motion.button>
              <motion.button
                className="btn btn--primary"
                onClick={() => void answer(true)}
                {...tactile()}
                autoFocus
              >
                Tak, zrób to
              </motion.button>
            </motion.div>
          ) : (
            <motion.div
              key="answered"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={SPRING.panel}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "flex-end",
                gap: 8,
                color: answered ? "hsl(var(--state-ok))" : "var(--ink-soft)",
              }}
            >
              <motion.span
                initial={{ pathLength: 0, scale: 0.6 }}
                animate={{ scale: 1 }}
                transition={SPRING.panel}
                style={{ fontSize: 18 }}
              >
                {answered ? "✓" : "✕"}
              </motion.span>
              {answered ? "Robię." : "Dobrze, nie robię tego."}
            </motion.div>
          )}
        </AnimatePresence>
      </Glass>
    </motion.div>
  );
}

/**
 * How many blocked tasks Home pins above the thread. Kept in step with
 * `Home.tsx`; if the two disagree, the worst case is an approval offered twice
 * rather than one offered nowhere.
 */
const PINNED_ON_HOME = 3;

/**
 * Floating layer: approvals sit above whatever panel is open.
 *
 * It deliberately says nothing about an approval the current screen is already
 * asking about. One request used to produce three sets of "Zgoda / Nie" — in
 * the conversation, on the pinned task card, and here — which is three chances
 * to wonder which button counted. The rule now: the screen answers if it can,
 * and this layer covers everywhere else.
 */
export function ApprovalLayer() {
  const approvals = useStore((s) => s.approvals);
  const view = useStore((s) => s.view);
  const tasks = useStore((s) => s.tasks);

  const blocked = tasks.filter((task) => task.state === "blocked");
  const onScreen = new Set(
    view === "tasks"
      ? blocked.map((task) => task.id)
      : view === "home"
        ? blocked.slice(0, PINNED_ON_HOME).map((task) => task.id)
        : [],
  );
  // An approval with no task cannot be shown anywhere else, so it always floats.
  const floating = approvals.filter(
    (approval) => !approval.task_id || !onScreen.has(approval.task_id),
  );

  return (
    <div
      style={{
        position: "fixed",
        right: 24,
        bottom: 24,
        display: "grid",
        gap: 12,
        justifyItems: "end",
        zIndex: 40,
        pointerEvents: "none",
      }}
    >
      <AnimatePresence>
        {floating.map((approval) => (
          <div key={approval.id} style={{ pointerEvents: "auto" }}>
            <Card approval={approval} />
          </div>
        ))}
      </AnimatePresence>
    </div>
  );
}

/** Compact count for the navigation, animated so a new request is noticed. */
export function ApprovalBadge({ count }: { count: number }) {
  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.span
          initial={{ scale: 0, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0, opacity: 0 }}
          transition={SPRING.panel}
          className="tiny"
          style={{
            minWidth: 20,
            height: 20,
            padding: "0 6px",
            borderRadius: "var(--radius-pill)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            background: "hsl(38 92% 62% / 0.28)",
            border: "1px solid hsl(38 92% 62% / 0.5)",
          }}
        >
          <motion.span
            animate={{ opacity: [1, 0.55, 1] }}
            transition={{ duration: AMBIENT.attention, ...LOOP }}
          >
            {count}
          </motion.span>
        </motion.span>
      )}
    </AnimatePresence>
  );
}

export const approvalTransition = TWEEN.content;
