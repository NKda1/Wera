import { defineConfig, devices } from "@playwright/test";

// End-to-end journeys in demo mode (SPEC §12). Starts its own server so demo state is fresh.
// Set PW_CHANNEL=chrome to use an installed Chrome instead of Playwright's Chromium.
const PORT = 3200;

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
  webServer: {
    command: `next dev -p ${PORT}`,
    url: `http://localhost:${PORT}/jobs`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { WERA_DEMO_MODE: "true", LLM_API_KEY: "", FIRECRAWL_API_KEY: "", ALERTS_IMAP_APP_PASSWORD: "" },
  },
});
