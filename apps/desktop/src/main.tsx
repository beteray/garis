import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { DEFAULT_APPEARANCE, applyAppearance } from "./lib/appearance";
import { mount as mountFixture } from "./dev/fixture";
import "./styles/global.css";

// Before the first paint, with defaults: the engine's real settings arrive a
// round trip later, and a window that flashes dark then light on every open
// looks broken no matter how good the theme it settles on is.
applyAppearance(DEFAULT_APPEARANCE);

// Development-only visual states for screenshots. A production build compiles
// this to nothing, because the gate inside it is a build-time literal.
mountFixture();

// Native window vibrancy (Mica/Acrylic) is set by the Tauri shell; when it is
// present the CSS gradient fallback stays out of the way so the blur has real
// desktop behind it.
const root = document.getElementById("root")!;
if ((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__) {
  root.dataset.vibrancy = "true";
}

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
