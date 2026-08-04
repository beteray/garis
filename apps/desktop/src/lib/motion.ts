/**
 * The motion system. Every moving thing in GARIS reads from this file.
 *
 * The rule that shapes it: motion is information, not decoration. Each preset
 * below answers "what does this communicate?" — arrival, departure, hierarchy,
 * feedback, or a state the engine reported. Anything that communicates nothing
 * is not here.
 *
 * Two hard constraints, both from bitter experience rather than taste:
 *
 *   1. **Transform and opacity only.** Animating height, width, top or left
 *      makes the browser re-lay-out the page on every frame. The old
 *      `listItemVariants` animated `height` and `marginBottom`, which is why a
 *      task list with a dozen rows stuttered whenever one arrived.
 *
 *   2. **Reduced motion is decided in one place.** `lib/appearance.ts` resolves
 *      the OS preference and the GARIS setting into `data-motion`, and every
 *      variant here degrades from that single answer. Framer Motion writes
 *      inline styles, so the stylesheet cannot stop it — the JS has to ask.
 *
 * Dials for this application (see the brief): DESIGN_VARIANCE 7,
 * MOTION_INTENSITY 8, VISUAL_DENSITY 5. High motion intensity here means
 * *considered* movement with real spring physics, not more of it: an agent
 * window that sits open all day has to be calm at rest.
 */

import type { Transition, Variants } from "framer-motion";

// ---------------------------------------------------------------- the curve

/**
 * The house curve: quick to commit, long to settle.
 *
 * One easing curve for almost everything is what makes an interface feel like a
 * single object rather than a pile of components. This is the iOS deceleration
 * feel, and it is the same value as `--ease-glass` in tokens.css — the two must
 * not drift, so the CSS reads it from here in spirit and the tests pin it.
 */
export const EASE: [number, number, number, number] = [0.32, 0.72, 0, 1];

/** For things leaving. Slightly front-loaded so exits feel decisive. */
export const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];

// ------------------------------------------------------------- the durations

/**
 * Durations by *what the movement is for*, never by how long it looks.
 *
 * A component that wants "fast" has to say what it is doing, which is what
 * stops a codebase from accumulating fourteen slightly different 200ms values.
 */
export const DURATION = {
  /** Press feedback, focus rings, hover. Below this it reads as a glitch. */
  instant: 0.12,
  /** Buttons, toggles, segmented controls, chips. */
  control: 0.18,
  /** Messages, cards, list rows, tooltips. */
  content: 0.28,
  /** Panels, sheets, expanding details. */
  panel: 0.4,
  /** Route changes and anything that moves across the window. */
  spatial: 0.52,
} as const;

// --------------------------------------------------------------- the springs

/**
 * Springs by role. Restrained damping throughout: this is a desktop tool, and
 * bounce on a task card reads as a toy.
 */
export const SPRING = {
  /** Controls and small feedback. Crisp, no visible overshoot. */
  control: { type: "spring", stiffness: 420, damping: 38, mass: 0.8 },
  /** Cards, messages, rows. A trace of settle, never a bounce. */
  content: { type: "spring", stiffness: 320, damping: 34, mass: 0.9 },
  /** Panels and sheets. Heavier, so large surfaces feel like they have mass. */
  panel: { type: "spring", stiffness: 210, damping: 30, mass: 1 },
  /** Things that travel across the window — the nav selection pill. */
  spatial: { type: "spring", stiffness: 260, damping: 32, mass: 1 },
} as const satisfies Record<string, Transition>;

/** Tween presets, for where a spring would be wrong (opacity, blur, colour). */
export const TWEEN = {
  instant: { duration: DURATION.instant, ease: EASE },
  control: { duration: DURATION.control, ease: EASE },
  content: { duration: DURATION.content, ease: EASE },
  panel: { duration: DURATION.panel, ease: EASE },
  spatial: { duration: DURATION.spatial, ease: EASE },
  exit: { duration: DURATION.control, ease: EASE_OUT },
} as const satisfies Record<string, Transition>;

/**
 * Ambient loops — the orb and the atmosphere.
 *
 * Deliberately slow. Anything that repeats forever within a person's peripheral
 * vision has to be slower than they can track, or it becomes the only thing
 * they can see.
 */
export const AMBIENT = {
  /** One breath of the idle orb. */
  breath: 5.2,
  /** The drift of the background aurora. */
  aurora: 38,
  /** A waiting-for-you edge pulse. Slow enough to read as patience. */
  waiting: 2.8,
  /** A working orb's internal circulation. */
  working: 2.2,
  /** Something asking to be noticed: the lock on an approval, a pending badge. */
  attention: 2.4,
  /** A live status dot — the connection pill, the presence marker.
   *
   *  It used to be 1.6s, written inline where no rule could see it. Naming it
   *  here put it under the two-second floor for anything that repeats forever,
   *  and the floor is right: a dot blinking once a second in the corner of a
   *  window that stays open all day is a window nobody can stop looking at. */
  dot: 2.4,
  /** The slow travel of a sheen across a large glass surface. */
  sheen: 4.5,
  /** A skeleton's shimmer while real content is on its way. Also raised to the
   *  floor: a fast shimmer reads as urgency, and waiting is not urgent. */
  loading: 2.2,
} as const;

/** The loop every ambient animation shares: forever, and eased at both ends. */
export const LOOP = { repeat: Infinity, ease: "easeInOut" } as const;

/** A counter travelling to its new value. Softer than SPRING.content: a number
 *  that overshoots reads as a number that changed twice. */
export const COUNTER = { stiffness: 140, damping: 20 } as const;

// ------------------------------------------------------------ reduced motion

/**
 * Does the person want less movement?
 *
 * Read from `data-motion`, which `lib/appearance.ts` has already resolved from
 * the OS preference and the GARIS setting together. Asking `matchMedia` again
 * here would mean components and stylesheet could disagree — and someone who
 * turned animation off inside GARIS would still get a moving orb.
 */
export const prefersReducedMotion = (): boolean =>
  typeof document !== "undefined" && document.documentElement.dataset.motion === "off";

/**
 * Reduced motion is not "no animation" — it is "no *movement*".
 *
 * Opacity still carries the change, so nothing appears or vanishes abruptly and
 * the user can still tell that something happened. Every variant set below has
 * a still counterpart, and `variants()` is the only way to reach either.
 */
const STILL: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: DURATION.control } },
  // `pointerEvents` on the way out for the same reason as the scrim below: a
  // thing that has finished being visible must stop being clickable first.
  exit: { opacity: 0, pointerEvents: "none", transition: { duration: DURATION.instant } },
};

// ---------------------------------------------------------------- the sets

/**
 * A route arriving. Rises and resolves out of a slight blur, which is what
 * makes a view change read as depth rather than as a page load.
 *
 * The blur is on the panel only, never on a list of many elements: a filter is
 * the most expensive thing here and it earns its place exactly once per change.
 */
const panel: Variants = {
  hidden: { opacity: 0, y: 12, scale: 0.99, filter: "blur(5px)" },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    filter: "blur(0px)",
    transition: { ...TWEEN.panel },
  },
  exit: {
    opacity: 0,
    y: -8,
    scale: 0.995,
    filter: "blur(3px)",
    transition: TWEEN.exit,
  },
};

/**
 * A row joining or leaving a list.
 *
 * Note what is *not* animated: height and margin. Framer's `layout` prop moves
 * the surrounding rows using transforms, which costs nothing per frame; the old
 * version animated the box model and made every arrival a reflow.
 */
const row: Variants = {
  hidden: { opacity: 0, x: -10, scale: 0.985 },
  visible: { opacity: 1, x: 0, scale: 1, transition: SPRING.content },
  exit: { opacity: 0, x: 12, scale: 0.985, transition: TWEEN.exit },
};

/** A message arriving in the conversation. Rises slightly, as if it landed. */
const message: Variants = {
  hidden: { opacity: 0, y: 8, scale: 0.985 },
  visible: { opacity: 1, y: 0, scale: 1, transition: SPRING.content },
  exit: { opacity: 0, scale: 0.99, transition: TWEEN.exit },
};

/** A card in a grid or column. */
const card: Variants = {
  hidden: { opacity: 0, y: 14, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: SPRING.panel },
  exit: { opacity: 0, y: -10, scale: 0.985, transition: TWEEN.content },
};

/**
 * A dialog taking over. Comes forward rather than up, because it is above the
 * page rather than after it.
 *
 * The scale is deliberately shallow. Combined with `transform-origin` set from
 * the control that opened it (see components/Dialog.tsx) it reads as the dialog
 * growing out of its opener; a deeper zoom from the centre of the screen is the
 * generic effect this replaces, and it points at nothing.
 */
const dialog: Variants = {
  hidden: { opacity: 0, scale: 0.96, y: 6 },
  visible: { opacity: 1, scale: 1, y: 0, transition: SPRING.panel },
  exit: { opacity: 0, scale: 0.985, y: 3, pointerEvents: "none", transition: TWEEN.exit },
};

/**
 * The scrim behind a dialog. Opacity only — it covers the whole window.
 *
 * It stops taking clicks the instant it starts leaving. Framer keeps the node
 * mounted for the length of the exit, and a scrim that is 4% opaque still
 * swallows the click aimed at whatever is now visible through it.
 */
const scrim: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, pointerEvents: "auto", transition: TWEEN.control },
  exit: { opacity: 0, pointerEvents: "none", transition: TWEEN.control },
};

/** A sheet sliding in from the edge: the overlay navigation, approval sheets. */
const sheet: Variants = {
  hidden: { opacity: 0, x: -22 },
  visible: { opacity: 1, x: 0, transition: SPRING.panel },
  exit: { opacity: 0, x: -18, transition: TWEEN.exit },
};

/** Something the engine is asking for. Arrives with weight, so it is noticed. */
const attention: Variants = {
  hidden: { opacity: 0, y: -8, scale: 0.98 },
  visible: { opacity: 1, y: 0, scale: 1, transition: SPRING.panel },
  exit: { opacity: 0, y: -6, scale: 0.99, transition: TWEEN.exit },
};

const SETS = { panel, row, message, card, dialog, scrim, sheet, attention } as const;

export type MotionSet = keyof typeof SETS;

/**
 * The only way to get variants.
 *
 * Called at render, so a person toggling "Animacje: wyłączone" gets stillness on
 * the very next paint rather than after a reload. This is what makes the old
 * `motionSafe` helper — which existed and was never once called — unnecessary.
 */
export function variants(set: MotionSet): Variants {
  return prefersReducedMotion() ? STILL : SETS[set];
}

/**
 * Children appear in sequence, not all at once — reads as intent, not a dump.
 *
 * Capped: past about eight items the stagger stops being elegant and starts
 * being a wait, so long lists get the same small total delay as short ones.
 */
/**
 * The delay for item `index` in a hand-rolled sequence, capped so a long list
 * does not become a wait. For anything that can use `stagger()` below, use that
 * instead — this is for lists whose children cannot carry variants.
 */
export function step(index: number, each = 0.04, cap = 0.3): number {
  if (prefersReducedMotion()) return 0;
  return Math.min(index * each, cap);
}

export function stagger(count = 8): Variants {
  if (prefersReducedMotion()) return { hidden: {}, visible: {} };
  const each = count > 8 ? 0.02 : 0.045;
  return {
    hidden: {},
    visible: { transition: { staggerChildren: each, delayChildren: 0.03 } },
  };
}

// ------------------------------------------------------------- interaction

/**
 * Press and hover feedback for anything clickable.
 *
 * Spread onto a `motion` element. One definition means a button in Settings and
 * a button in the conversation answer the pointer identically, which is most of
 * what "feels finished" actually is.
 */
export function tactile(options: { lift?: boolean; disabled?: boolean } = {}) {
  const { lift = true, disabled = false } = options;
  if (disabled || prefersReducedMotion()) return {};
  // A control told not to lift must not mention `y` at all — `y: 0` is not
  // movement, but it is a claim about the axis, and something that must stay
  // put should not be making claims about the axis it stays put on.
  return lift
    ? ({
        whileHover: { y: -1, scale: 1.012 },
        whileTap: { y: 0, scale: 0.975 },
        transition: SPRING.control,
      } as const)
    : ({
        whileHover: { scale: 1.012 },
        whileTap: { scale: 0.975 },
        transition: SPRING.control,
      } as const);
}

/** Press feedback only — for things that must not move under the pointer,
 *  like a colour swatch in a row of swatches. */
export function pressable(disabled = false) {
  if (disabled || prefersReducedMotion()) return {};
  return { whileTap: { scale: 0.94 }, transition: SPRING.control } as const;
}

// ------------------------------------------------- compatibility, deliberate

/**
 * Kept because `layoutId` transitions and a handful of one-off `transition=`
 * props read better with a bare preset than with `SPRING.spatial` spelled out.
 * These are aliases into the table above, not a second vocabulary.
 */
export const spring: Transition = SPRING.content;
export const spatialSpring: Transition = SPRING.spatial;
export const quick: Transition = TWEEN.control;
export const base: Transition = TWEEN.content;
export const slow: Transition = TWEEN.spatial;
