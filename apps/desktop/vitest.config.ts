import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    // The accessibility suite reads the stylesheets as text (`?raw`) to check
    // that focus rings are still there. Without this, Vitest hands back an
    // empty string for anything CSS and those checks silently pass on nothing.
    css: true,
  },
});
