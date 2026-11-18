import { defineConfig, devices } from "@playwright/test";

/**
 * One browser, one spec, and a mocked API.
 *
 * The API is mocked on purpose rather than for convenience. F-06 is entirely a
 * client-side defect -- the server has accepted `new_parent_id: null` since
 * M2, and the move algorithm has handled it under a row lock since then. What
 * was broken was the *client's inability to say it*. So the thing worth
 * asserting is the request the browser sends, not the response it gets back,
 * and intercepting the route asserts exactly that with no database, no cold
 * start and no shared state between runs.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // `--host 127.0.0.1` is load-bearing, not tidiness. `vite preview`
    // otherwise binds `localhost`, which on a CI runner resolves to `::1`
    // first -- so the server is up and `http://127.0.0.1:4173` is still
    // refused, and Playwright reports it as a two-minute start-up timeout
    // rather than as an address mismatch. That error names the wrong cause,
    // which is the expensive kind.
    command: "npm run build && npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    // The command type-checks and builds before it serves, so this covers a
    // cold `tsc` on a slow runner as well as the server itself.
    timeout: 180_000,
  },
});
