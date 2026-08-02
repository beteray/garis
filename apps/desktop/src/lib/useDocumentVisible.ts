/**
 * Is anybody looking?
 *
 * A hidden window still runs its animations, and an agent that lives in the
 * tray is hidden most of the time. Everything expensive asks this first: the
 * atmosphere, the state indicator, anything on a timer.
 *
 * `data-quiet` on the root is set alongside, because the stylesheet needs the
 * same answer for the background it owns and cannot ask a hook.
 */

import { useEffect, useState } from "react";

const look = () => typeof document === "undefined" || !document.hidden;

export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(look);

  useEffect(() => {
    const onChange = () => {
      const now = look();
      setVisible(now);
      document.documentElement.dataset.quiet = now ? "false" : "true";
    };
    onChange();
    // Minimising, hiding to the tray and switching virtual desktops all land
    // here. Deliberately *not* window blur: an agent whose progress you are
    // watching from behind another window should keep showing progress.
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);

  return visible;
}
