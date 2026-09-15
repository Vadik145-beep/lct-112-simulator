import { defineConfig, devices } from "@playwright/test";

// End-to-end checks against a running stand (docker compose up). Self-signed TLS is expected.
const baseURL = process.env.E2E_BASE_URL ?? "https://localhost";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    ignoreHTTPSErrors: true,
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
