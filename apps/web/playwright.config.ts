import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// End-to-end journeys in demo mode (SPEC §12). Starts its own agent and web servers so demo
// state is fresh. Set PW_CHANNEL=chrome to use an installed Chrome instead of Playwright's Chromium.
const PORT = 3200;
const AGENT_PORT = 8799;
const AGENT_DIR = resolve(__dirname, "../../services/agent");
const E2E_DB = resolve(__dirname, "../../data/e2e.db");
const AGENT_ENV = { AGENT_PORT: String(AGENT_PORT), AGENT_URL: `http://127.0.0.1:${AGENT_PORT}`, AGENT_TOKEN: "e2e-agent-token" };

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } }, testIgnore: /phone\.spec\.ts/ },
    { name: "phone", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } }, testMatch: /phone\.spec\.ts/ },
  ],
  // The agent first, on a fresh demo database, then the web app pointed at it.
  webServer: [
    {
      command: `uv run --project ${AGENT_DIR} wera reset --yes && uv run --project ${AGENT_DIR} wera serve`,
      url: `http://127.0.0.1:${AGENT_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { ...AGENT_ENV, WERA_DEMO_MODE: "true", DATABASE_URL: `sqlite:///${E2E_DB}`, LLM_API_KEY: "", FIRECRAWL_API_KEY: "", ALERTS_IMAP_APP_PASSWORD: "" },
    },
    {
      command: `next dev -p ${PORT}`,
      url: `http://localhost:${PORT}/jobs`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: AGENT_ENV,
    },
  ],
});
