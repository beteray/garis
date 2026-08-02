/**
 * Shared motion vocabulary.
 *
 * Every transition in the app comes from this file. Mixing ad-hoc durations and
 * curves is what makes an interface feel assembled from parts instead of
 * designed — one spring and one easing curve, used everywhere, is what makes it
 * feel like a single object.
 */

import type { Transition, Variants } from "framer-motion";

/** The house curve: quick to commit, long to settle (iOS deceleration). */
export const EASE: [number, number, number, number] = [0.32, 0.72, 0, 1];

export const spring: Transition = {
  type: "spring",
  stiffness: 320,
  damping: 34,
  mass: 0.9,
};

export const softSpring: Transition = {
  type: "spring",
  stiffness: 180,
  damping: 26,
  mass: 1,
};

export const quick: Transition = { duration: 0.18, ease: EASE };
export const base: Transition = { duration: 0.28, ease: EASE };
export const slow: Transition = { duration: 0.52, ease: EASE };

/** Does the person want less movement? Checked at render, honoured everywhere.
 *
 * Read from `data-motion`, which `lib/appearance.ts` has already resolved from
 * the OS preference and the GARIS setting together. Asking `matchMedia` again
 * here would mean components and stylesheet could disagree — and someone who
 * turned animation off inside GARIS would still get a spinning orb. */
export const prefersReducedMotion = () =>
  typeof document !== "undefined" &&
  document.documentElement.dataset.motion === "off";

/**
 * Reduced motion is not "no animation" — it is "no *movement*".
 * Opacity still carries the change, so nothing appears or vanishes abruptly.
 */
export const motionSafe = <T extends Variants>(full: T, reduced: T): T =>
  prefersReducedMotion() ? reduced : full;

export const panelVariants: Variants = {
  hidden: { opacity: 0, y: 14, scale: 0.985, filter: "blur(6px)" },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    filter: "blur(0px)",
    transition: { ...base, duration: 0.32 },
  },
  exit: {
    opacity: 0,
    y: -10,
    scale: 0.99,
    filter: "blur(4px)",
    transition: quick,
  },
};

export const reducedPanelVariants: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.18 } },
  exit: { opacity: 0, transition: { duration: 0.12 } },
};

export const listItemVariants: Variants = {
  hidden: { opacity: 0, x: -12, height: 0, marginBottom: 0 },
  visible: {
    opacity: 1,
    x: 0,
    height: "auto",
    marginBottom: 10,
    transition: spring,
  },
  exit: {
    opacity: 0,
    x: 14,
    height: 0,
    marginBottom: 0,
    transition: { ...quick, duration: 0.22 },
  },
};

export const messageVariants: Variants = {
  hidden: { opacity: 0, y: 10, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: spring },
  exit: { opacity: 0, scale: 0.98, transition: quick },
};

/** Children appear in sequence, not all at once — reads as intent, not a dump. */
export const staggerContainer: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.045, delayChildren: 0.03 } },
};

export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0, transition: base },
};

export const cardVariants: Variants = {
  hidden: { opacity: 0, y: 18, scale: 0.97 },
  visible: { opacity: 1, y: 0, scale: 1, transition: softSpring },
  exit: { opacity: 0, y: -12, scale: 0.98, transition: base },
};
