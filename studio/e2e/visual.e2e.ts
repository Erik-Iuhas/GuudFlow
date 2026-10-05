import { readFileSync } from "node:fs";
import { expect, test, type Page, type Route } from "@playwright/test";
import { REPORT_FIXTURE } from "../src/features/report/__fixtures__/report";

// Visual snapshots of the main screens, light and dark, with the backend mocked.
// Baselines live in e2e/__screenshots__ (see snapshotPathTemplate in playwright.config.ts).
// Regenerate them deliberately with `npm run e2e -- visual --update-snapshots`.

test.use({ viewport: { width: 1280, height: 800 } });

const THEMES = ["light", "dark"] as const;
type Theme = (typeof THEMES)[number];

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/** Pin the theme before the app boots (lib/theme reads this key) and match the media query. */
async function setTheme(page: Page, theme: Theme): Promise<void> {
  await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
  await page.addInitScript((t) => window.localStorage.setItem("guudflow.theme", t), theme);
}

/** Wait for fonts and layout to settle, then compare. */
async function snap(page: Page, name: string, theme: Theme): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  await expect(page).toHaveScreenshot(`${name}-${theme}.png`);
}

// ── fixtures ────────────────────────────────────────────────────────────────

const DASHBOARD = {
  active_runs: [
    {
      run_id: "run_20260301_a1",
      project_id: "billing-api",
      project_name: "Billing API",
      goal: "Add refresh-token rotation to the auth flow",
      status: "running",
      current_agents: [],
      started_at: "2026-03-01T10:00:00",
      elapsed_s: 754,
      task_progress: { closed: 3, total: 7 },
    },
  ],
  recent_completions: [
    { run_id: "run_20260228_f3", project_id: "billing-api", outcome: "complete", duration_s: 1260 },
    { run_id: "run_20260227_c9", project_id: "docs-site", outcome: "failed", duration_s: 312 },
    { run_id: "run_20260226_b2", project_id: "cli-tool", outcome: "complete", duration_s: 845 },
  ],
  pending_approvals: [
    { run_id: "run_20260301_a1", approval_id: "ap1", title: "Install the pytest-cov package", kind: "package" },
  ],
  top_personas: [],
};

const PROJECTS = [
  {
    project_id: "billing-api",
    goal: "Add refresh-token rotation to the auth flow",
    agents: [
      { role: "planner", model_tier: "heavy" },
      { role: "backend", model_tier: "solid", depends_on: ["planner"] },
      { role: "reviewer", model_tier: "solid", depends_on: ["backend"] },
    ],
    rbac: {},
    max_iterations: 5,
  },
  {
    project_id: "docs-site",
    goal: "Rewrite the getting-started guide",
    agents: [{ role: "writer", model_tier: "fast" }],
    rbac: {},
    max_iterations: 3,
  },
];

const LEVELS = {
  sandbox: ["strict", "standard", "host_dev"],
  web: ["off", "search", "search+fetch"],
  shell: ["readonly", "project", "ask"],
  packages: ["off", "via_it_agent"],
  subagents: ["off", "explore_only", "full"],
  browser: ["off", "screenshot"],
  vision: ["off", "on"],
};
const BASE_CAPS = {
  sandbox: "standard", web: "off", shell: "project", packages: "via_it_agent",
  subagents: "explore_only", browser: "off", vision: "off",
};

const cockpit = JSON.parse(
  readFileSync(new URL("../src/features/cockpit/__fixtures__/run2000.json", import.meta.url), "utf8"),
) as {
  meta: { run_id: string; project_id: string };
  project: unknown;
  cards: unknown[];
  quips: unknown[];
  signals: unknown[];
};
const COCKPIT_RUN = cockpit.meta.run_id;

async function mockRunSockets(page: Page, run: string, signals: unknown[], quips: unknown[] = []): Promise<void> {
  await page.routeWebSocket(new RegExp(`/ws/runs/${run}$`), (ws) => {
    ws.send(JSON.stringify({ type: "snapshot", live: false, signals }));
  });
  await page.routeWebSocket(new RegExp(`/ws/runs/${run}/quips$`), (ws) => {
    ws.send(JSON.stringify({ type: "snapshot", live: false, quips }));
  });
}

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

const boardCard = (id: string, column: string, priority: number, owner = "dev") => ({
  id, title: `Task ${id}`, owner, column, kind: "code", size: "S", priority, review_round: 0,
  acceptance: [`${id} works`], depends_on: [], column_since: null, thread: [],
});

// ── pages ───────────────────────────────────────────────────────────────────

const PAGES: Array<{
  name: string;
  run: (page: Page, theme: Theme) => Promise<void>;
}> = [
  {
    name: "dashboard",
    run: async (page) => {
      await page.route("**/api/dashboard", (r) => json(r, DASHBOARD));
      await page.goto("/");
      await expect(page.getByText("Recent completions")).toBeVisible();
      await expect(page.getByText("run_20260228_f3")).toBeVisible();
    },
  },
  {
    name: "projects",
    run: async (page) => {
      await page.route("**/api/projects", (r) => json(r, PROJECTS));
      await page.route("**/api/example-projects", (r) => json(r, []));
      await page.route("**/api/runs", (r) => json(r, []));
      await page.goto("/projects");
      await expect(page.getByText("billing-api").first()).toBeVisible();
    },
  },
  {
    name: "project-work",
    run: async (page) => {
      const now = 1_780_000_000;
      await page.route(/\/api\/(projects|runs|models)(\/|\?|$)/, (r) => json(r, { detail: "not mocked" }, 404));
      await page.route("**/api/projects/billing-api", (r) =>
        json(r, { project_id: "billing-api", goal: "A billing API", agents: [], harness: "opencode", approval_gates: {} }),
      );
      await page.route(/\/api\/runs\?project_id=/, (r) => json(r, []));
      await page.route("**/api/models**", (r) => json(r, []));
      await page.route("**/api/projects/billing-api/runs-review", (r) =>
        json(r, {
          main: "a".repeat(40),
          runs: [
            { run_id: "run_7c2e1", state: "pending", base: "a", head: "b", commits: 4,
              files: ["api/auth.py", "api/tokens.py", "tests/test_auth.py"], submitted_at: now,
              decided_at: null, merged: "", note: "", parent_run: null },
            { run_id: "run_5a91d", state: "accepted", base: "c", head: "d", commits: 2, files: ["api/app.py"],
              submitted_at: now - 86400, decided_at: now - 80000, merged: "d", note: "first slice", parent_run: null },
          ],
        }),
      );
      const item = (id: string, title: string, over: Record<string, unknown> = {}) => ({
        id, title, acceptance: [], kind: "code", size: "M", priority: 0, status: "open", notes: "", files_hint: [],
        source: "human", created_at: now, updated_at: now, run_id: null, pending: null, history: [], ...over,
      });
      await page.route("**/api/projects/billing-api/backlog", (r) =>
        json(r, {
          items: [
            item("B3", "Refresh-token rotation", { status: "in_run", run_id: "run_7c2e1", pending: "done",
              acceptance: ["Old refresh token is rejected after use"] }),
            item("B4", "Rate-limit the login endpoint", { priority: 3, acceptance: ["429 after 5 failed tries in a minute"],
              history: [{ run_id: "run_5a91d", summary: "Added the limiter middleware; tests still missing" }] }),
            item("B6", "Invoice PDF export", { priority: 2, kind: "code", size: "L" }),
            item("B7", "Document the auth flow", { priority: 1, kind: "docs", size: "S", source: "run:run_7c2e1" }),
            item("B1", "Health endpoint", { status: "done" }),
          ],
        }),
      );
      await page.goto("/projects/billing-api");
      await expect(page.getByTestId("review-run_7c2e1")).toBeVisible();
      await expect(page.getByTestId("backlog-B4")).toBeVisible();
    },
  },
  {
    name: "oneshot",
    run: async (page) => {
      await page.route("**/api/example-projects", (r) =>
        json(r, [
          {
            id: "cli", title: "CLI tool", category: "build", icon: "x", summary: "A small CLI",
            details: "", default_project_id: "cli", default_goal: "Build a CSV to JSON CLI",
            requires: {}, has_seed_inputs: false,
          },
        ]),
      );
      await page.route("**/api/harness/capabilities", (r) =>
        json(r, {
          levels: LEVELS, role_types: ["builder"], defaults: { builder: BASE_CAPS },
          host_dev_warning: "host_dev runs on your machine", production: false,
        }),
      );
      await page.goto("/oneshot");
      await expect(page.getByTestId("example-cli")).toBeVisible();
    },
  },
  {
    name: "propose",
    run: async (page) => {
      await page.route("**/api/harness/capabilities", (r) =>
        json(r, {
          levels: LEVELS, role_types: ["builder"], defaults: { builder: BASE_CAPS },
          host_dev_warning: "host_dev runs on your machine", production: false,
        }),
      );
      await page.route("**/api/harness/capabilities/resolve", (r) =>
        json(r, { role_type: "builder", capabilities: BASE_CAPS, vision_available: true, errors: [], warnings: [] }),
      );
      await page.route("**/api/meta/propose", (r) =>
        json(r, {
          rationale: "A builder writes the change and a reviewer checks it against the acceptance list.",
          agents: [
            { role: "builder", description: "Writes code", depends_on: [], model_tier: "solid" },
            {
              role: "reviewer", description: "Reviews", depends_on: ["builder"], model_tier: "solid",
              is_reviewer: true, review_of: ["builder"], max_review_rounds: 3,
            },
          ],
          rbac: {},
          playbook_entries_used: [],
        }),
      );
      await page.goto("/propose");
      await page.getByPlaceholder(/e\.g\./).fill("Improve the CLI");
      await page.getByRole("button", { name: "Propose team" }).click();
      await expect(page.getByTestId("team-node-builder")).toBeVisible();
      await expect(page.getByTestId("team-node-reviewer")).toBeVisible();
      await page.waitForTimeout(600); // React Flow fitView
    },
  },
  {
    name: "cockpit",
    run: async (page) => {
      await page.route(/\/api\/(runs|projects)\//, (r) => json(r, { detail: "not mocked" }, 404));
      await page.route(`**/api/runs/${COCKPIT_RUN}`, (r) => json(r, cockpit.meta));
      await page.route(`**/api/projects/${cockpit.meta.project_id}`, (r) => json(r, cockpit.project));
      await page.route(`**/api/runs/${COCKPIT_RUN}/cards`, (r) => json(r, { cards: cockpit.cards }));
      await mockRunSockets(page, COCKPIT_RUN, cockpit.signals, cockpit.quips);
      await page.goto(`/runs/${COCKPIT_RUN}`);
      await expect(page.getByTestId("cockpit")).toBeVisible();
      await expect(page.getByTestId("graph-node-planner")).toHaveAttribute("data-state", "complete");
      await expect(page.getByTestId("board-feed").getByText("run complete").first()).toBeVisible();
      await page.waitForTimeout(600); // React Flow fitView
    },
  },
  {
    name: "report",
    run: async (page) => {
      const run = REPORT_FIXTURE.run_id;
      await page.route(/\/api\/(runs|projects)\//, (r) => json(r, { detail: "not mocked" }, 404));
      await page.route(`**/api/runs/${run}/report`, (r) => json(r, REPORT_FIXTURE));
      await page.route(`**/api/runs/${run}/cards`, (r) => json(r, { cards: [] }));
      await mockRunSockets(page, run, []);
      await page.goto(`/runs/${run}/report`);
      await expect(page.getByTestId("run-report")).toBeVisible();
      await expect(page.getByTestId("cost-table")).toBeVisible();
    },
  },
  {
    name: "board",
    run: async (page) => {
      const cards = [
        boardCard("T1", "todo", 2), boardCard("T2", "todo", 1), boardCard("T3", "in_progress", 0),
        boardCard("T4", "review", 0, "reviewer"), boardCard("T5", "done", 0),
      ];
      await page.route("**/api/runs/r1/cards", (r) => json(r, { cards }));
      await mockRunSockets(page, "r1", []);
      await page.goto("/runs/r1/board");
      await expect(page.getByTestId("card-T1")).toBeVisible();
      await expect(page.getByTestId("card-T5")).toBeVisible();
    },
  },
  {
    name: "artifacts",
    run: async (page) => {
      const run = "run-visual";
      const artifact = (id: string, path: string, kind: string, at: string) => ({
        id, type: "ARTIFACT", summary: path, from_agent: "frontend", priority: "normal", iteration: 0, timestamp: at,
        detail: JSON.stringify({
          path, kind, role: "frontend", task_id: null, size: 1234, sha256: "0".repeat(64), created_at: at,
          served_path: `artifacts/frontend/${path}`,
        }),
      });
      await page.route(`**/api/runs/${run}/cards`, (r) => json(r, { cards: [] }));
      await page.route(`**/api/runs/${run}/artifact-files/**`, (r) =>
        r.fulfill({ status: 200, contentType: "image/png", body: PNG, headers: { "X-Content-Type-Options": "nosniff" } }),
      );
      await mockRunSockets(page, run, [
        artifact("a1", "home.png", "image", "2026-05-01T10:00:00+00:00"),
        artifact("a2", "settings.png", "image", "2026-05-01T11:00:00+00:00"),
        artifact("a3", "report.html", "html", "2026-05-01T12:00:00+00:00"),
      ]);
      await page.goto(`/runs/${run}/artifacts`);
      await expect(page.getByTestId("artifact-tile")).toHaveCount(3);
    },
  },
];

for (const theme of THEMES) {
  test.describe(`visual (${theme})`, () => {
    for (const p of PAGES) {
      test(p.name, async ({ page }) => {
        await setTheme(page, theme);
        await p.run(page, theme);
        await snap(page, p.name, theme);
      });
    }
  });
}
