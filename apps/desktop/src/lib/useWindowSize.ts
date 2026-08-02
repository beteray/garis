/**
 * The window's own size, as state.
 *
 * A media query in CSS cannot tell React which navigation shape to render, and
 * duplicating the breakpoints in a `matchMedia` call per component is how the
 * two get out of step. One listener, one number, shared.
 */

import { useEffect, useState } from "react";

const read = () => ({
  width: typeof window === "undefined" ? 1440 : window.innerWidth,
  height: typeof window === "undefined" ? 900 : window.innerHeight,
});

export function useWindowSize(): { width: number; height: number } {
  const [size, setSize] = useState(read);

  useEffect(() => {
    let frame = 0;
    const onResize = () => {
      // Coalesce to one update per frame: a dragged window edge fires resize
      // faster than React can render, and every one of those was a full tree.
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setSize(read()));
    };
    window.addEventListener("resize", onResize);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return size;
}
