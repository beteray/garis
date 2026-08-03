/**
 * Primary navigation, in three shapes.
 *
 * The shapes are the same list rendered differently, never a different list:
 * a destination that disappears when the window narrows is a feature the user
 * has to widen the window to discover. Labels become tooltips and accessible
 * names, and nothing else changes.
 *
 * No layout jump on collapse: the rail and the full nav are both flex children
 * of the shell with their own width, and the workspace has `min-width: 0`, so
 * the transition is one animated width rather than a reflow of everything.
 */

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";
import type { Destination, NavShape, Route } from "../lib/nav";
import { SPRING, tactile, variants } from "../lib/motion";

interface NavProps {
  shape: NavShape;
  destinations: Destination[];
  current: Route;
  onNavigate: (route: Route) => void;
  /** Live counts, so the nav can say "two things need you" without a click. */
  badges?: Partial<Record<Route, number>>;
  /** Overlay only. */
  open?: boolean;
  onClose?: () => void;
  header?: React.ReactNode;
  footer?: React.ReactNode;
}

function NavItem({
  destination,
  active,
  showLabel,
  badge,
  onSelect,
}: {
  destination: Destination;
  active: boolean;
  showLabel: boolean;
  badge?: number;
  onSelect: () => void;
}) {
  return (
    <motion.button
      onClick={onSelect}
      {...tactile({ lift: false })}
      className="btn btn--quiet no-drag nav-item"
      // The accessible name survives the label being hidden. `title` gives the
      // sighted keyboard user the same thing as a tooltip.
      aria-label={destination.label}
      aria-current={active ? "page" : undefined}
      title={showLabel ? destination.hint : `${destination.label} — ${destination.hint}`}
      data-active={active || undefined}
    >
      {active && (
        <motion.span
          layoutId="nav-active"
          transition={SPRING.spatial}
          aria-hidden
          className="nav-item__pill"
        />
      )}
      <span className="nav-item__icon" aria-hidden>
        {destination.icon}
      </span>
      {showLabel && <span className="nav-item__label">{destination.label}</span>}
      {badge !== undefined && badge > 0 && (
        <span
          className="nav-item__badge tiny"
          // Read as part of the item rather than as a stray number.
          aria-label={`${badge} do obejrzenia`}
        >
          {badge}
        </span>
      )}
    </motion.button>
  );
}

export function Nav({
  shape,
  destinations,
  current,
  onNavigate,
  badges = {},
  open = false,
  onClose,
  header,
  footer,
}: NavProps) {
  const panel = useRef<HTMLDivElement | null>(null);
  const opener = useRef<Element | null>(null);

  // Overlay navigation is a dialog: it traps focus, Escape closes it, and focus
  // goes back where it came from. Anything less strands a keyboard user inside
  // a menu they cannot leave.
  useEffect(() => {
    if (shape !== "overlay" || !open) return;
    opener.current = document.activeElement;
    const first = panel.current?.querySelector<HTMLElement>("button");
    first?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose?.();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const focusable = panel.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable.length) return;
      const [head] = focusable;
      const tail = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === head) {
        event.preventDefault();
        tail.focus();
      } else if (!event.shiftKey && document.activeElement === tail) {
        event.preventDefault();
        head.focus();
      }
    };

    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [shape, open, onClose]);

  const items = (showLabel: boolean) =>
    destinations.map((destination) => (
      <NavItem
        key={destination.id}
        destination={destination}
        active={current === destination.id}
        showLabel={showLabel}
        badge={badges[destination.id]}
        onSelect={() => {
          onNavigate(destination.id);
          onClose?.();
        }}
      />
    ));

  if (shape === "overlay") {
    return (
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              className="nav-scrim"
              variants={variants("scrim")}
              initial="hidden"
              animate="visible"
              exit="exit"
              onClick={onClose}
              aria-hidden
            />
            <motion.div
              ref={panel}
              role="dialog"
              aria-modal="true"
              aria-label="Nawigacja"
              className="glass glass--raised nav nav--overlay"
              variants={variants("sheet")}
              initial="hidden"
              animate="visible"
              exit="exit"
            >
              {header}
              <nav aria-label="Główna nawigacja" className="nav__list">
                {items(true)}
              </nav>
              <div style={{ flex: 1 }} />
              {footer}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    );
  }

  return (
    <div
      className="glass drag-region nav"
      data-shape={shape}
      // Width is the only thing that animates; the workspace flexes to fill.
      style={{ width: shape === "rail" ? "var(--nav-rail)" : "var(--nav-width)" }}
    >
      {header}
      <nav aria-label="Główna nawigacja" className="nav__list">
        {items(shape === "labels")}
      </nav>
      <div style={{ flex: 1 }} />
      {footer}
    </div>
  );
}

/** The button that opens the overlay. Only rendered when there is an overlay. */
export function NavButton({ onOpen, pending }: { onOpen: () => void; pending: number }) {
  return (
    <button
      className="btn btn--quiet no-drag"
      onClick={onOpen}
      aria-label={pending > 0 ? `Menu, ${pending} do obejrzenia` : "Menu"}
      aria-haspopup="dialog"
    >
      <span aria-hidden>☰</span>
      {pending > 0 && <span className="nav-item__badge tiny">{pending}</span>}
    </button>
  );
}
