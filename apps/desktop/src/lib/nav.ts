/**
 * The routes, and how the navigation adapts to the window.
 *
 * Six primary destinations, chosen by what a person actually comes here to do.
 * Conversation is *not* one of them: it is the main thing GARIS does, so it is
 * Home rather than a tab you switch to — the repository evidence is that every
 * event which matters (a question, an approval, a report) is already delivered
 * into the chat stream by `store.handleEvent`. A separate route would mean the
 * daily surface is the one you have to navigate away from Home to reach.
 *
 * Automations is absent on purpose. Nothing in `core/src/garis` schedules
 * anything: there is a `subscription.item` topic and no producer, no scheduler
 * table, no source registry. The old panel was `useState` and hardcoded strings
 * pretending to be settings. A route that cannot do the thing it is named after
 * is worse than no route.
 *
 * Diagnostics, the tool catalogue, the audit trail and raw model data live
 * behind Developer Mode for the same reason: they are machinery, and putting
 * machinery in the primary navigation tells the user it is something they are
 * supposed to operate.
 */

export type Route =
  | "home"
  | "tasks"
  | "memory"
  | "connections"
  | "settings"
  | "developer";

export interface Destination {
  id: Route;
  label: string;
  /** Kept as a glyph rather than an icon font: one less thing to fail to load. */
  icon: string;
  /** What this screen answers, for the tooltip in rail mode. */
  hint: string;
  /** Only shown when the user has turned Developer Mode on. */
  developerOnly?: boolean;
}

export const DESTINATIONS: Destination[] = [
  { id: "home", label: "Start", icon: "◎", hint: "Rozmowa i co się teraz dzieje" },
  { id: "tasks", label: "Zadania", icon: "◷", hint: "Co robię i co wymaga Ciebie" },
  { id: "memory", label: "Pamięć", icon: "🧠", hint: "Co pamiętam i co trzymam w sejfie" },
  { id: "connections", label: "Połączenia", icon: "🖥", hint: "Urządzenia i co na nich potrafię" },
  { id: "settings", label: "Ustawienia", icon: "⚙", hint: "Wygląd, modele, prywatność" },
  {
    id: "developer",
    label: "Diagnostyka",
    icon: "◔",
    hint: "Narzędzia, dziennik, surowe dane",
    developerOnly: true,
  },
];

export const routesFor = (developerMode: boolean): Destination[] =>
  DESTINATIONS.filter((destination) => developerMode || !destination.developerOnly);

/** How the navigation is drawn right now. */
export type NavShape = "labels" | "rail" | "overlay";

/** Where the shape changes. Chosen from the layout, not from device categories:
 *  1024 CSS pixels is a 1536-pixel laptop at 150% Windows scaling, which is an
 *  ordinary desktop, not a tablet. */
export const RAIL_BELOW = 1120;
export const OVERLAY_BELOW = 820;

/**
 * Resolve the user's preference against the window.
 *
 * "auto" adapts; an explicit choice is honoured until the window is genuinely
 * too narrow to hold it, at which point the overlay takes over — refusing to
 * adapt there would mean a navigation the user cannot reach, which is worse
 * than briefly ignoring their preference.
 */
export function navShape(preference: string, width: number): NavShape {
  if (width < OVERLAY_BELOW) return "overlay";
  if (preference === "rail") return "rail";
  if (preference === "labels") return "labels";
  return width < RAIL_BELOW ? "rail" : "labels";
}
