/**
 * Browser smoke tests (D-026 Q5).
 *
 *   npm test            the built web app against a local API over the synthetic region
 *                       (needs the PostGIS container: docker compose up -d db)
 *   E2E_BASE_URL=https://luangia.github.io/touch-grass/ npm run test:live
 *                       the same paths against the deployed site
 */
import { defineConfig, devices } from "@playwright/test";

const LIVE = process.env.E2E_BASE_URL;
const API = "http://127.0.0.1:8100";
const WEB = "http://127.0.0.1:4173";
// Served under the same sub-path as GitHub Pages, so base-path routing is tested too.
const BASE = "/touch-grass/";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  globalTeardown: LIVE ? undefined : "./teardown.ts",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { trace: "retain-on-failure", ...devices["Desktop Chrome"] },
  projects: LIVE
    ? [{ name: "live", use: { baseURL: LIVE } }]
    : [{ name: "local", use: { baseURL: `${WEB}${BASE}` } }],
  webServer: LIVE
    ? undefined
    : [
        {
          command: "uv run --project ../api python serve.py",
          url: `${API}/health`,
          timeout: 180_000,
          reuseExistingServer: !process.env.CI,
          stdout: "pipe",
        },
        {
          command:
            "npm --prefix ../web run build && npm --prefix ../web run preview -- --host 127.0.0.1",
          url: `${WEB}${BASE}`,
          timeout: 180_000,
          reuseExistingServer: !process.env.CI,
          env: { VITE_API_URL: API, VITE_BASE: BASE },
        },
      ],
});
