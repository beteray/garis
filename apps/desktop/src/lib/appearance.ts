/**
 * The only thing in the application allowed to decide how anything looks.
 *
 * It takes the engine's `appearance` settings, resolves the two that depend on
 * the machine rather than the person — "system" theme and "system" animation —
 * and writes the result onto the document root as attributes and custom
 * properties. Every stylesheet reads from there and nowhere else.
 *
 * Why a resolver and not `@media (prefers-color-scheme)`: with the media query,
 * the light palette has to be written twice — once for the query, once for the
 * explicit setting — and the two drift. Worse, an explicit "light" could not
 * win over a dark OS at all. Resolving in one place makes `data-theme` the
 * single truth and lets the stylesheet stay honest with one palette each.
 */

export type Theme = "system" | "dark" | "light";

export interface Appearance {
  theme: Theme;
  accent: string;
  glass: number;
  density: "comfortable" | "compact";
  text_scale: number;
  animation: "system" | "full" | "off";
  orb: "full" | "simple" | "off";
  navigation: "labels" | "rail";
  sounds: boolean;
  high_contrast: boolean;
}

export const DEFAULT_APPEARANCE: Appearance = {
  theme: "system",
  accent: "cyan",
  glass: 1,
  density: "comfortable",
  text_scale: 1,
  animation: "system",
  orb: "full",
  navigation: "labels",
  sounds: false,
  high_contrast: false,
};

/** Mirrors `ACCENTS` in `core/src/garis/config.py`. Kept as HSL triples so the
 *  alpha the glass needs composes on top without a second colour space. */
export const ACCENTS: Record<string, string> = {
  cyan: "196 95% 60%",
  blue: "212 92% 64%",
  violet: "268 85% 68%",
  teal: "168 78% 52%",
  green: "148 70% 52%",
  amber: "38 92% 62%",
  rose: "348 88% 66%",
};

const prefers = (query: string) =>
  typeof window !== "undefined" && window.matchMedia?.(query).matches === true;

export const systemTheme = (): "dark" | "light" =>
  prefers("(prefers-color-scheme: light)") ? "light" : "dark";

export const systemWantsStillness = (): boolean =>
  prefers("(prefers-reduced-motion: reduce)");

/** `#rrggbb` → the `H S% L%` triple the tokens expect. */
export function hexToHsl(hex: string): string | null {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return null;
  const value = parseInt(match[1], 16);
  const [r, g, b] = [(value >> 16) & 255, (value >> 8) & 255, value & 255].map(
    (channel) => channel / 255,
  );
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  const span = max - min;
  if (span === 0) return `0 0% ${Math.round(lightness * 100)}%`;
  const saturation = span / (1 - Math.abs(2 * lightness - 1));
  const hue =
    max === r
      ? ((g - b) / span + (g < b ? 6 : 0)) * 60
      : max === g
        ? ((b - r) / span + 2) * 60
        : ((r - g) / span + 4) * 60;
  return `${Math.round(hue)} ${Math.round(saturation * 100)}% ${Math.round(
    lightness * 100,
  )}%`;
}

export const accentTriple = (accent: string): string =>
  ACCENTS[accent] ?? hexToHsl(accent) ?? ACCENTS.cyan;

/**
 * Write one appearance onto the document. Idempotent, and cheap enough to call
 * on every settings change and every system preference change.
 */
export function applyAppearance(
  look: Partial<Appearance>,
  root: HTMLElement | null = typeof document === "undefined" ? null : document.documentElement,
): void {
  if (!root) return;
  const settings = { ...DEFAULT_APPEARANCE, ...look };

  root.dataset.theme = settings.theme === "system" ? systemTheme() : settings.theme;
  root.dataset.density = settings.density;
  root.dataset.navigation = settings.navigation;
  root.dataset.orb = settings.orb;
  // One attribute, already resolved: no component should have to ask the OS
  // and the settings separately and then agree with itself.
  root.dataset.motion =
    settings.animation === "off" || (settings.animation === "system" && systemWantsStillness())
      ? "off"
      : "full";
  root.dataset.contrast = settings.high_contrast ? "high" : "normal";

  root.style.setProperty("--accent-hsl", accentTriple(settings.accent));
  const glass = clamp(settings.glass, 0, 1);
  root.style.setProperty("--glass-strength", glass.toString());
  // At zero, take the backdrop filters off entirely rather than blurring by
  // zero pixels: a no-op filter still costs a compositing pass, and "wyłącz
  // szkło" is usually asked for by someone whose machine is struggling.
  root.dataset.glass = glass === 0 ? "off" : "on";
  root.style.setProperty("--text-scale", clamp(settings.text_scale, 0.9, 1.4).toString());
}

const clamp = (value: number, low: number, high: number) =>
  Number.isFinite(value) ? Math.min(high, Math.max(low, value)) : low;

/**
 * Re-apply when the machine changes its mind — the OS theme flips at sunset,
 * or accessibility settings change while the window is open. Returns a cleanup.
 */
export function watchSystemAppearance(reapply: () => void): () => void {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const queries = [
    window.matchMedia("(prefers-color-scheme: light)"),
    window.matchMedia("(prefers-reduced-motion: reduce)"),
  ];
  for (const query of queries) query.addEventListener("change", reapply);
  return () => {
    for (const query of queries) query.removeEventListener("change", reapply);
  };
}
