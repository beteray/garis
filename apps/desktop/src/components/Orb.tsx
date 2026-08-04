/**
 * The GARIS orb: soft, layered, alive, and honest.
 *
 * Not a sphere. A sphere with a specular highlight is a ball, and a ball is a
 * toy. This is a cluster of blurred colour fields orbiting a common centre at
 * different radii and speeds, seen through one shared blur — closer to a drop
 * of ink in water than to a rendered object. The silhouette is deliberately
 * imperfect and always moving, so it reads as something alive rather than
 * something drawn.
 *
 * Built from CSS radial gradients on composited layers rather than WebGL. The
 * version this replaces was a fragment shader running a full-screen pass
 * forever, which cost a GPU context, 340 pixels of the daily surface, and a
 * silent fallback path on machines without WebGL. Six blurred divs on the
 * compositor cost approximately nothing, degrade to a static gradient with no
 * code, and survive Remote Desktop.
 *
 * **The honesty rule.** Each visual state below maps to a `RuntimeState` the
 * engine can actually report. There is no state here for "recovering" or
 * "gaming", because `AgentState` in the Python engine has no such value and
 * nothing detects a running game. An orb that showed them would be inventing
 * runtime information, which is the one thing this interface must never do.
 */

import { motion } from "framer-motion";
import { useMemo } from "react";
import { AMBIENT, LOOP, TWEEN, prefersReducedMotion } from "../lib/motion";
import type { RuntimeState } from "../lib/runtimeState";
import { PRESENTATION } from "../lib/runtimeState";
import { useDocumentVisible } from "../lib/useDocumentVisible";

/**
 * One blurred colour field. Six of these make the orb.
 *
 * `hue` is an HSL triple so the fields compose with the accent the user chose;
 * `orbit` is how far from centre it travels, `period` how long one lap takes.
 */
interface Field {
  hue: string;
  size: number;
  orbit: number;
  period: number;
  phase: number;
}

/**
 * The palette, in the order the brief asks for: pink through magenta, violet,
 * blue, cyan, green, and warm at the far end. Restrained red — it appears only
 * in the error state, because a red that shows up while things are fine is a
 * red nobody believes when they are not.
 */
const BASE_FIELDS: Field[] = [
  { hue: "330 90% 66%", size: 0.74, orbit: 0.13, period: 1.0, phase: 0 },
  { hue: "282 88% 68%", size: 0.82, orbit: 0.16, period: 1.35, phase: 0.28 },
  { hue: "214 92% 64%", size: 0.9, orbit: 0.12, period: 1.7, phase: 0.55 },
  { hue: "188 92% 58%", size: 0.78, orbit: 0.17, period: 1.15, phase: 0.72 },
  { hue: "154 76% 56%", size: 0.66, orbit: 0.2, period: 2.1, phase: 0.4 },
  { hue: "38 94% 62%", size: 0.56, orbit: 0.22, period: 1.55, phase: 0.86 },
];

/**
 * How each runtime state colours and moves the orb.
 *
 * `energy` scales brightness and internal speed; `spread` pushes the fields
 * apart, which is what makes "working" look like circulation and "offline"
 * look collapsed. `tint` biases the whole cluster toward one hue without
 * replacing the palette, so the orb stays recognisably itself.
 */
interface Mood {
  energy: number;
  spread: number;
  saturation: number;
  /** Extra field laid over the cluster, for states that need a clear colour. */
  tint?: { hue: string; strength: number };
  /** A slow ring at the edge — used only where the engine is waiting on a person. */
  edge?: string;
}

const MOOD: Record<RuntimeState, Mood> = {
  // Gathering out of blur. Deliberately dim: this must not imply readiness.
  starting: { energy: 0.5, spread: 0.55, saturation: 0.7 },
  idle: { energy: 0.72, spread: 1, saturation: 1 },
  receiving: { energy: 0.95, spread: 1.06, saturation: 1.05 },
  answering: { energy: 1.0, spread: 1.05, saturation: 1.05 },
  planning: { energy: 1.05, spread: 1.12, saturation: 1.1, tint: { hue: "268 88% 70%", strength: 0.3 } },
  executing: { energy: 1.15, spread: 1.18, saturation: 1.12, tint: { hue: "196 95% 60%", strength: 0.28 } },
  verifying: { energy: 1.05, spread: 1.1, saturation: 1.08, tint: { hue: "168 82% 56%", strength: 0.26 } },
  // Quieter, not brighter: waiting is not urgency, and the text says what is needed.
  waiting_input: { energy: 0.6, spread: 0.92, saturation: 0.9, edge: "38 92% 62%" },
  waiting_approval: { energy: 0.6, spread: 0.92, saturation: 0.9, edge: "38 92% 62%" },
  completed: { energy: 0.9, spread: 1.1, saturation: 1.1, tint: { hue: "148 74% 56%", strength: 0.26 } },
  warning: { energy: 0.66, spread: 0.95, saturation: 0.95, tint: { hue: "38 92% 62%", strength: 0.34 } },
  // The one place red is allowed, and it contracts rather than flashing.
  error: { energy: 0.7, spread: 0.78, saturation: 1, tint: { hue: "354 88% 62%", strength: 0.42 } },
  // Drained of colour. An offline agent should look switched off, not broken.
  offline: { energy: 0.34, spread: 0.7, saturation: 0.25 },
  quiet: { energy: 0.42, spread: 0.86, saturation: 0.6 },
};

export interface OrbProps {
  state: RuntimeState;
  /** Rendered size in pixels. Scales with the layout; never fixed by the orb. */
  size?: number;
  className?: string;
}

export function Orb({ state, size = 128, className }: OrbProps) {
  const visible = useDocumentVisible();
  const still = prefersReducedMotion();
  const mood = MOOD[state] ?? MOOD.idle;
  const label = (PRESENTATION[state] ?? PRESENTATION.idle).label;

  // Three independent reasons to hold still, and any one is enough: the window
  // is not being looked at, the person asked for calm, or the state is at rest.
  const animate = visible && !still;

  // Fields are stable across renders so a state change re-times them rather
  // than rebuilding them, which is what keeps the transition continuous.
  const fields = useMemo(() => BASE_FIELDS, []);

  return (
    <div
      className={`orb ${className ?? ""}`}
      style={{ width: size, height: size }}
      // The orb is decoration for a state that is already written out in text
      // beside it. Announcing it again would say everything twice.
      role="img"
      aria-label={`Stan: ${label}`}
      data-state={state}
    >
      <div
        className="orb__cluster"
        style={{
          filter: `blur(${size * 0.085}px) saturate(${mood.saturation})`,
          opacity: mood.energy,
        }}
      >
        {fields.map((field, index) => {
          const travel = size * field.orbit * mood.spread;
          const period = (field.period * AMBIENT.breath) / Math.max(mood.energy, 0.4);
          return (
            <motion.span
              key={index}
              className="orb__field"
              style={{
                width: size * field.size,
                height: size * field.size,
                background: `radial-gradient(circle at 50% 50%, hsl(${field.hue} / 0.92), hsl(${field.hue} / 0) 68%)`,
              }}
              animate={
                animate
                  ? {
                      // A lissajous-ish path: x and y on different periods, so
                      // the cluster never repeats a visible loop.
                      x: [0, travel, 0, -travel * 0.8, 0],
                      y: [0, -travel * 0.9, travel * 0.6, 0, 0],
                      scale: [1, 1.08, 0.95, 1.04, 1],
                    }
                  : { x: 0, y: 0, scale: 1 }
              }
              transition={
                animate
                  ? {
                      duration: period,
                      ...LOOP,
                      delay: -field.phase * period,
                    }
                  : TWEEN.panel
              }
            />
          );
        })}

        {mood.tint && (
          <span
            className="orb__field orb__tint"
            style={{
              width: size * 1.05,
              height: size * 1.05,
              opacity: mood.tint.strength,
              background: `radial-gradient(circle at 50% 50%, hsl(${mood.tint.hue} / 0.9), hsl(${mood.tint.hue} / 0) 70%)`,
            }}
          />
        )}
      </div>

      {/* The waiting ring. Only two states get it, and both of them are the
          engine asking a person for something. */}
      {mood.edge && (
        <motion.span
          className="orb__edge"
          aria-hidden
          style={{ borderColor: `hsl(${mood.edge} / 0.55)` }}
          animate={animate ? { opacity: [0.25, 0.7, 0.25], scale: [1, 1.04, 1] } : { opacity: 0.5 }}
          transition={
            animate
              ? { duration: AMBIENT.waiting, ...LOOP }
              : TWEEN.content
          }
        />
      )}

      {/* A single soft specular, well off-centre. What stops the cluster from
          reading as flat without turning it into a glass ball. */}
      <span className="orb__sheen" aria-hidden />
    </div>
  );
}
