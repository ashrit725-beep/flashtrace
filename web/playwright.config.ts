import { defineConfig, devices } from "@playwright/test";

// Tests run against the static export (web/out, built by `pretest:a11y`)
// served by a dependency-free node server. The API is mocked per test in the
// browser (tests/mock-api.ts), so no Python server is needed.
export default defineConfig({
  testDir: "tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4317",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "node tests/serve-static.mjs",
    url: "http://127.0.0.1:4317/",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
