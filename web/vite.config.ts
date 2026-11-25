import { copyFileSync } from "node:fs";
import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

/**
 * Static hosts answer an unknown path (a deep link like /touch-grass/rankings) with
 * 404.html. Making that file the app itself lets it boot and read the path (D-026 Q3).
 */
function spaFallback(): Plugin {
  return {
    name: "spa-404-fallback",
    apply: "build",
    closeBundle() {
      const out = resolve(__dirname, "dist");
      copyFileSync(resolve(out, "index.html"), resolve(out, "404.html"));
    },
  };
}

export default defineConfig({
  // "/" locally; "/touch-grass/" for GitHub Pages (set by the Pages workflow).
  base: process.env.VITE_BASE ?? "/",
  plugins: [react(), spaFallback()],
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
  test: { environment: "jsdom", include: ["tests/**/*.test.{ts,tsx}"] },
});
