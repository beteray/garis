/**
 * GARIS itself, on screen.
 *
 * One continuous form that never stands still — even idle, it breathes. States do
 * not swap, they *morph*: the shader's parameters are eased toward their targets
 * every frame, so thinking becomes working the way a mood changes, not the way a
 * slide advances.
 *
 * Rendered in WebGL because the alternative — stacked blurred divs — cannot do
 * organic turbulence at 60 fps. There is a Canvas 2D fallback for machines
 * without WebGL, and rendering pauses entirely when the window is hidden, because
 * this thing runs all day in the background.
 */

import { useEffect, useRef, useState } from "react";
import type { AgentState } from "../lib/api";
import { prefersReducedMotion } from "../lib/motion";

interface Look {
  hue: number;        // degrees
  hue2: number;       // secondary hue for the rim
  energy: number;     // 0..1 overall brightness/presence
  turbulence: number; // how much the surface churns
  speed: number;      // time multiplier
  rings: number;      // orbiting detail (thinking/working)
  breathe: number;    // slow scale oscillation
}

/**
 * The state table. This is the visual half of the contract in docs/UI.md; the
 * other half is `agent.state` on the event stream.
 */
const LOOKS: Record<AgentState, Look> = {
  idle:      { hue: 196, hue2: 214, energy: 0.42, turbulence: 0.22, speed: 0.30, rings: 0.0, breathe: 1.0 },
  listening: { hue: 165, hue2: 188, energy: 0.78, turbulence: 0.34, speed: 0.75, rings: 0.5, breathe: 0.5 },
  thinking:  { hue: 268, hue2: 292, energy: 0.72, turbulence: 0.85, speed: 1.15, rings: 1.0, breathe: 0.3 },
  working:   { hue: 196, hue2: 172, energy: 0.86, turbulence: 0.55, speed: 0.95, rings: 0.75, breathe: 0.35 },
  verifying: { hue: 186, hue2: 210, energy: 0.70, turbulence: 0.40, speed: 0.70, rings: 0.55, breathe: 0.4 },
  speaking:  { hue: 210, hue2: 190, energy: 0.92, turbulence: 0.45, speed: 1.0,  rings: 0.35, breathe: 0.45 },
  blocked:   { hue: 42,  hue2: 28,  energy: 0.62, turbulence: 0.25, speed: 0.42, rings: 0.2,  breathe: 0.8 },
  done:      { hue: 148, hue2: 168, energy: 0.88, turbulence: 0.28, speed: 0.55, rings: 0.15, breathe: 0.7 },
  failed:    { hue: 8,   hue2: 22,  energy: 0.74, turbulence: 0.62, speed: 0.85, rings: 0.25, breathe: 0.6 },
};

const VERTEX = `
attribute vec2 aPosition;
void main() {
  gl_Position = vec4(aPosition, 0.0, 1.0);
}`;

const FRAGMENT = `
precision highp float;

uniform vec2  uRes;
uniform float uTime;
uniform float uHue;
uniform float uHue2;
uniform float uEnergy;
uniform float uTurb;
uniform float uRings;
uniform float uBreathe;
uniform float uLevel;   // microphone amplitude, 0..1
uniform float uStill;   // 1.0 when the person asked for reduced motion

float hash(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
    mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x),
    u.y);
}

float fbm(vec2 p) {
  float total = 0.0;
  float amplitude = 0.5;
  for (int i = 0; i < 5; i++) {
    total += noise(p) * amplitude;
    p *= 2.02;
    amplitude *= 0.5;
  }
  return total;
}

vec3 hsl2rgb(float h, float s, float l) {
  vec3 rgb = clamp(abs(mod(h / 60.0 + vec3(0.0, 4.0, 2.0), 6.0) - 3.0) - 1.0, 0.0, 1.0);
  return l + s * (rgb - 0.5) * (1.0 - abs(2.0 * l - 1.0));
}

void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / min(uRes.x, uRes.y);
  float r = length(uv);
  float angle = atan(uv.y, uv.x);

  // uStill freezes motion without flattening the form: the shape stays, the
  // churn stops. Reduced motion must never mean "a static grey circle".
  float t = uTime * (1.0 - uStill);

  float churn = fbm(uv * 2.6 + vec2(t * 0.17, -t * 0.13));
  float breath = sin(t * 0.55) * 0.018 * uBreathe;

  float radius = 0.36 + breath
               + (churn - 0.5) * 0.20 * uTurb
               + uLevel * 0.07;

  // Core: soft filled body that fades toward the edge.
  float core = smoothstep(radius, radius - 0.30, r);

  // Rim: where the light catches. This is what makes it read as a sphere
  // rather than a blurred dot.
  float rim = exp(-pow((r - radius) * 8.5, 2.0));

  // Orbiting detail — visible while thinking and working, absent at rest.
  float orbit = 0.0;
  if (uRings > 0.01) {
    for (int i = 0; i < 3; i++) {
      float fi = float(i);
      float speed = 0.5 + fi * 0.27;
      float band = radius + 0.055 + fi * 0.035;
      float wobble = sin(angle * (3.0 + fi) + t * speed * 2.0) * 0.012;
      orbit += exp(-pow((r - band - wobble) * 34.0, 2.0)) * (0.5 - fi * 0.12);
    }
    orbit *= uRings;
  }

  // Listening rings: driven by real microphone amplitude, so the orb reacts to
  // the voice rather than pretending to.
  float voice = 0.0;
  if (uLevel > 0.01) {
    float wave = sin(r * 26.0 - t * 4.2) * 0.5 + 0.5;
    voice = wave * exp(-pow((r - radius - 0.10) * 9.0, 2.0)) * uLevel * 1.4;
  }

  vec3 inner = hsl2rgb(uHue, 0.86, 0.60);
  vec3 outer = hsl2rgb(uHue2, 0.92, 0.66);

  vec3 colour = inner * core * (0.55 + churn * 0.45);
  colour += outer * rim * 1.25;
  colour += outer * orbit * 1.5;
  colour += mix(inner, outer, 0.5) * voice;

  float alpha = clamp(core * 0.92 + rim * 0.85 + orbit + voice, 0.0, 1.0);
  colour *= uEnergy * 1.25;

  gl_FragColor = vec4(colour, alpha * uEnergy);
}`;

function compile(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

export interface OrbProps {
  state: AgentState;
  /** Microphone amplitude 0..1. */
  level?: number;
  size?: number;
  className?: string;
}

export function Orb({ state, level = 0, size = 340, className }: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef(state);
  const levelRef = useRef(level);
  // State, not a ref: a ref mutated inside the effect never re-renders, so the
  // fallback would stay hidden forever on exactly the machines that need it.
  const [fallback, setFallback] = useState(false);

  stateRef.current = state;
  levelRef.current = level;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const gl =
      (canvas.getContext("webgl", { alpha: true, premultipliedAlpha: false }) as
        | WebGLRenderingContext
        | null) ?? null;

    if (!gl) {
      setFallback(true);
      return;
    }

    const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX);
    const fragment = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT);
    const program = gl.createProgram();
    if (!vertex || !fragment || !program) {
      setFallback(true);
      return;
    }

    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      setFallback(true);
      return;
    }
    gl.useProgram(program);

    // One triangle covering the viewport beats two for a full-screen pass.
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 3, -1, -1, 3]),
      gl.STATIC_DRAW,
    );
    const position = gl.getAttribLocation(program, "aPosition");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const uniforms = {
      uRes: gl.getUniformLocation(program, "uRes"),
      uTime: gl.getUniformLocation(program, "uTime"),
      uHue: gl.getUniformLocation(program, "uHue"),
      uHue2: gl.getUniformLocation(program, "uHue2"),
      uEnergy: gl.getUniformLocation(program, "uEnergy"),
      uTurb: gl.getUniformLocation(program, "uTurb"),
      uRings: gl.getUniformLocation(program, "uRings"),
      uBreathe: gl.getUniformLocation(program, "uBreathe"),
      uLevel: gl.getUniformLocation(program, "uLevel"),
      uStill: gl.getUniformLocation(program, "uStill"),
    };

    const still = prefersReducedMotion() ? 1 : 0;
    const current: Look = { ...LOOKS[stateRef.current] };
    let smoothedLevel = 0;
    let frame = 0;
    let running = true;
    const started = performance.now();

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.round(canvas.clientWidth * ratio);
      const height = Math.round(canvas.clientHeight * ratio);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        gl.viewport(0, 0, width, height);
      }
    };

    const render = () => {
      if (!running) return;
      resize();

      const target = LOOKS[stateRef.current] ?? LOOKS.idle;
      // Morph, never cut. 0.055 is slow enough to read as a mood change and
      // fast enough that the orb is never lying about the current state.
      const ease = 0.055;
      current.hue += shortestHueStep(current.hue, target.hue) * ease;
      current.hue2 += shortestHueStep(current.hue2, target.hue2) * ease;
      current.energy += (target.energy - current.energy) * ease;
      current.turbulence += (target.turbulence - current.turbulence) * ease;
      current.speed += (target.speed - current.speed) * ease;
      current.rings += (target.rings - current.rings) * ease;
      current.breathe += (target.breathe - current.breathe) * ease;
      smoothedLevel += (levelRef.current - smoothedLevel) * 0.25;

      const elapsed = ((performance.now() - started) / 1000) * current.speed;

      gl.uniform2f(uniforms.uRes, canvas.width, canvas.height);
      gl.uniform1f(uniforms.uTime, elapsed);
      gl.uniform1f(uniforms.uHue, current.hue);
      gl.uniform1f(uniforms.uHue2, current.hue2);
      gl.uniform1f(uniforms.uEnergy, current.energy);
      gl.uniform1f(uniforms.uTurb, current.turbulence);
      gl.uniform1f(uniforms.uRings, current.rings);
      gl.uniform1f(uniforms.uBreathe, current.breathe);
      gl.uniform1f(uniforms.uLevel, smoothedLevel);
      gl.uniform1f(uniforms.uStill, still);

      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);

      frame = requestAnimationFrame(render);
    };

    // GARIS runs all day. Burning a GPU on an invisible window is the kind of
    // detail that decides whether people keep it running.
    const onVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(frame);
      } else if (!running) {
        running = true;
        frame = requestAnimationFrame(render);
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("resize", resize);
    frame = requestAnimationFrame(render);

    return () => {
      running = false;
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", resize);
      gl.deleteProgram(program);
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
      gl.deleteBuffer(buffer);
    };
  }, []);

  const look = LOOKS[state] ?? LOOKS.idle;

  return (
    <div
      className={className}
      style={{
        position: "relative",
        width: size,
        height: size,
        pointerEvents: "none",
      }}
    >
      {/* Ambient bloom under the canvas: cheap, and it makes the orb feel like
          it is lighting the panel rather than sitting on top of it. */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          inset: "-18%",
          borderRadius: "50%",
          background: `radial-gradient(circle, hsl(${look.hue} 90% 60% / 0.28), transparent 62%)`,
          filter: "blur(28px)",
          transition: "background 900ms cubic-bezier(0.32,0.72,0,1)",
        }}
      />
      <canvas
        ref={canvasRef}
        style={{
          width: "100%",
          height: "100%",
          display: fallback ? "none" : "block",
        }}
        role="img"
        aria-label={`GARIS: ${STATE_LABELS[state] ?? state}`}
      />
      {/* Fallback for machines without WebGL: a real, animated presence rather
          than an empty hole. */}
      <div
        aria-hidden
        className="orb-fallback"
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: "50%",
          display: fallback ? "block" : "none",
          background: `radial-gradient(circle at 42% 38%, hsl(${look.hue} 92% 66% / 0.95), hsl(${look.hue2} 88% 46% / 0.65) 46%, transparent 70%)`,
          animation: "orbBreathe 4.2s ease-in-out infinite",
        }}
      />
    </div>
  );
}

/** Hue is circular: 350° → 10° must go forward 20°, not backward 340°. */
function shortestHueStep(from: number, to: number): number {
  let delta = ((to - from) % 360 + 540) % 360 - 180;
  if (Math.abs(delta) < 0.01) delta = 0;
  return delta;
}

export const STATE_LABELS: Record<AgentState, string> = {
  idle: "czuwam",
  listening: "słucham",
  thinking: "myślę",
  working: "pracuję",
  verifying: "sprawdzam",
  speaking: "mówię",
  blocked: "czekam na Ciebie",
  done: "gotowe",
  failed: "nie udało się",
};
