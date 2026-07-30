import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tauri serves the built assets from disk, so relative paths are required.
export default defineConfig({
  plugins: [react()],
  base: "./",
  clearScreen: false,
  server: { port: 5183, strictPort: true },
  build: { target: "chrome110", outDir: "dist", emptyOutDir: true },
});
