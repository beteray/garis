/** Shared harness for component tests.
 *
 * jsdom has no matchMedia, no rAF and no scrollIntoView; all three are asked
 * for during an ordinary render, so they are stubbed once here rather than in
 * every test.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = ((callback: FrameRequestCallback) =>
    setTimeout(() => callback(0), 0) as unknown as number) as typeof requestAnimationFrame;
  window.cancelAnimationFrame = ((handle: number) =>
    clearTimeout(handle)) as typeof cancelAnimationFrame;
}

/** jsdom implements no PointerEvent, and Framer Motion's keyboard-press path
 *  constructs one when a `whileTap` element is activated with Enter or Space.
 *  Without this, every keyboard test raises an unhandled error that could mask
 *  a real one. A minimal subclass is enough: nothing under test reads the
 *  pointer-specific fields. */
if (typeof window.PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    constructor(type: string, params: PointerEventInit = {}) {
      super(type, params);
      this.pointerId = params.pointerId ?? 1;
      this.pointerType = params.pointerType ?? "mouse";
    }
  }
  window.PointerEvent = PointerEventPolyfill as unknown as typeof PointerEvent;
  globalThis.PointerEvent = window.PointerEvent;
}

Element.prototype.scrollIntoView = vi.fn();
Element.prototype.releasePointerCapture ??= vi.fn();
Element.prototype.hasPointerCapture ??= vi.fn(() => false);

afterEach(() => cleanup());
