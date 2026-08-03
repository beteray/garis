/** Shared pieces: glass surfaces, animated values, empty states, badges. */

import { AnimatePresence, motion, useMotionValue, useSpring } from "framer-motion";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { EASE, TWEEN, variants } from "../lib/motion";

/**
 * A glass surface that reacts to the pointer.
 *
 * The sheen following the cursor is what separates "a translucent rectangle"
 * from "a pane of glass" — a surface responds to where the light is.
 */
export function Glass({
  children,
  className = "",
  interactive = true,
  raised = false,
  style,
  ...rest
}: {
  children: ReactNode;
  className?: string;
  interactive?: boolean;
  raised?: boolean;
  style?: React.CSSProperties;
} & React.HTMLAttributes<HTMLDivElement>) {
  const ref = useRef<HTMLDivElement | null>(null);

  const onMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const node = ref.current;
    if (!node || !interactive) return;
    const rect = node.getBoundingClientRect();
    node.style.setProperty("--mx", `${event.clientX - rect.left}px`);
    node.style.setProperty("--my", `${event.clientY - rect.top}px`);
  };

  return (
    <div
      ref={ref}
      onPointerMove={onMove}
      className={[
        "glass",
        interactive ? "glass--interactive" : "",
        raised ? "glass--raised" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={style}
      {...rest}
    >
      {children}
    </div>
  );
}

/**
 * A number that travels to its new value instead of jumping.
 *
 * Counts in this app mean "how much work is in flight". A jump reads as a glitch;
 * a short travel reads as something happening.
 */
export function AnimatedNumber({
  value,
  duration = 0.5,
}: {
  value: number;
  duration?: number;
}) {
  const motionValue = useMotionValue(value);
  const spring = useSpring(motionValue, { stiffness: 140, damping: 20 });
  const [shown, setShown] = useState(value);

  useEffect(() => {
    motionValue.set(value);
  }, [value, motionValue]);

  useEffect(() => spring.on("change", (v) => setShown(Math.round(v))), [spring]);

  return <motion.span transition={{ duration, ease: EASE }}>{shown}</motion.span>;
}

/** Empty states get a presence of their own — never a bare sentence. */
export function Empty({
  icon,
  title,
  hint,
}: {
  icon: string;
  title: string;
  hint?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={TWEEN.content}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 10,
        padding: "48px 24px",
        textAlign: "center",
      }}
    >
      <motion.div
        aria-hidden
        animate={{ y: [0, -6, 0], opacity: [0.5, 0.75, 0.5] }}
        transition={{ duration: 4.5, repeat: Infinity, ease: "easeInOut" }}
        style={{ fontSize: 34 }}
      >
        {icon}
      </motion.div>
      <div style={{ fontWeight: 600 }}>{title}</div>
      {hint && <div className="soft tiny">{hint}</div>}
    </motion.div>
  );
}

const EFFECT_TONE: Record<string, string> = {
  payment: "var(--state-error)",
  publish: "var(--state-error)",
  send_message: "var(--state-error)",
  credentials: "var(--state-error)",
  delete_permanent: "var(--state-error)",
  elevate: "38 90% 60%",
  system_config: "38 90% 60%",
  install: "38 90% 60%",
};

/** Effect chips are colour-coded by consequence, not by category. */
export function EffectBadge({ effect, label }: { effect: string; label?: string }) {
  const tone = EFFECT_TONE[effect] ?? "var(--state-idle)";
  return (
    <span
      className="tiny"
      style={{
        padding: "2px 9px",
        borderRadius: "var(--radius-pill)",
        background: `hsl(${tone} / 0.16)`,
        border: `1px solid hsl(${tone} / 0.32)`,
        color: "var(--ink)",
        whiteSpace: "nowrap",
      }}
    >
      {label ?? effect}
    </span>
  );
}

export function Pill({
  children,
  active = false,
  onClick,
}: {
  children: ReactNode;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <motion.button
      className="btn tiny"
      onClick={onClick}
      whileTap={{ scale: 0.96 }}
      transition={TWEEN.control}
      style={{
        padding: "5px 12px",
        borderRadius: "var(--radius-pill)",
        background: active ? "var(--accent-soft)" : undefined,
        borderColor: active ? "hsl(var(--state-working) / 0.45)" : undefined,
      }}
    >
      {children}
    </motion.button>
  );
}

/** Loading is a shimmer, never a spinner. */
export function Skeleton({ height = 44, count = 3 }: { height?: number; count?: number }) {
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="skeleton" style={{ height }} />
      ))}
    </div>
  );
}

export function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <motion.section variants={variants("card")} style={{ display: "grid", gap: 12 }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <h2>{title}</h2>
        {action}
      </header>
      {children}
    </motion.section>
  );
}

/** Text that types itself in, for streamed replies. */
export function Typewriter({ text, speed = 14 }: { text: string; speed?: number }) {
  const [shown, setShown] = useState("");

  useEffect(() => {
    setShown("");
    let index = 0;
    const timer = setInterval(() => {
      index += 1;
      setShown(text.slice(0, index));
      if (index >= text.length) clearInterval(timer);
    }, speed);
    return () => clearInterval(timer);
  }, [text, speed]);

  return (
    <span>
      {shown}
      <AnimatePresence>
        {shown.length < text.length && (
          <motion.span
            initial={{ opacity: 0 }}
            animate={{ opacity: [0.2, 1, 0.2] }}
            exit={{ opacity: 0 }}
            transition={{ duration: 1.1, repeat: Infinity }}
            style={{
              display: "inline-block",
              width: 2,
              height: "1em",
              marginLeft: 2,
              background: "currentColor",
              verticalAlign: "text-bottom",
            }}
          />
        )}
      </AnimatePresence>
    </span>
  );
}

export function relativeTime(seconds?: number | null): string {
  if (!seconds) return "—";
  const delta = Date.now() / 1000 - seconds;
  if (delta < 60) return "przed chwilą";
  if (delta < 3600) return `${Math.round(delta / 60)} min temu`;
  if (delta < 86400) return `${Math.round(delta / 3600)} godz. temu`;
  return new Date(seconds * 1000).toLocaleDateString("pl-PL");
}
