import { defineConfig } from "@playwright/test";

// E2E against a mocked backend (route interception). The browser comes from
// PLAYWRIGHT_BROWSERS_PATH (e.g. /opt/pw-browsers); never run `playwright install`.
// Files are named *.e2e.ts so vitest (which matches *.spec.ts) never picks them up.
const PORT = Number(process.env["E2E_PORT"] ?? 3101);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: true,
  reporter: "list",
  // Visual baselines (e2e/visual.e2e.ts) are committed, one set for the single Chromium project.
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}{ext}",
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.02, animations: "disabled", caret: "hide" },
  },
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure" },
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env["CI"],
    timeout: 60_000,
  },
});
