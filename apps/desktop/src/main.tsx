import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/global.css";

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
