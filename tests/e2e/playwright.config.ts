import { defineConfig, devices } from "@playwright/test";

// Chromium's software GL rasterizer (SwiftShader) never produces a frame on some hosts (seen on WSL):
// requestAnimationFrame never fires, so Playwright's "stable" check cannot pass and clicks time out.
// Plain software compositing without SwiftShader renders at 60 fps there and everywhere else.
const RENDERING_ARGS = ["--disable-gpu", "--disable-gpu-compositing", "--disable-software-rasterizer"];
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 13765);
const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${WEB_PORT}`;

/**
 * Acceptance flows 1 and 2 (spec §96, §97) in a real browser against a running API + web.
 * Unless E2E_BASE_URL points at an already running stack, scripts/serve.sh boots both
 * with a throwaway data directory.
 */
export default defineConfig({
  testDir: "./specs",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  outputDir: "test-results",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 20_000,
    navigationTimeout: 180_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 }, launchOptions: { args: RENDERING_ARGS } },
    },
  ],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "bash scripts/serve.sh",
        url: baseURL,
        // serve.sh prints this after the web app answers (and, in dev mode, after route warm-up).
        wait: { stdout: /\[e2e\] web ready/ },
        reuseExistingServer: !process.env.CI,
        timeout: 900_000,
        stdout: "pipe",
        stderr: "pipe",
      },
});
