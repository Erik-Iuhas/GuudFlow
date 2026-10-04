import { expect, test, type Page, type Route } from "@playwright/test";

// One-shot launcher and team approval graph against a mocked backend (page.route).

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const LEVELS = {
  sandbox: ["strict", "standard", "host_dev"],
  web: ["off", "search", "search+fetch"],
  shell: ["readonly", "project", "ask"],
  packages: ["off", "via_it_agent"],
  subagents: ["off", "explore_only", "full"],
  browser: ["off", "screenshot"],
  vision: ["off", "on"],
};
const BASE = {
  sandbox: "standard", web: "off", shell: "project", packages: "via_it_agent",
  subagents: "explore_only", browser: "off", vision: "off",
};

async function mockCommon(page: Page) {
  await page.route("**/api/example-projects", (r) =>
    json(r, [{ id: "cli", title: "CLI tool", category: "build", icon: "x", summary: "A small CLI",
      details: "", default_project_id: "cli", default_goal: "Build a CSV to JSON CLI",
      requires: {}, has_seed_inputs: false }]));
  await page.route("**/api/harness/capabilities", (r) =>
    json(r, { levels: LEVELS, role_types: ["builder"], defaults: { builder: BASE },
      host_dev_warning: "host_dev runs on your machine", production: false }));
  await page.route("**/api/harness/capabilities/resolve", (r) => {
    const req = r.request().postDataJSON() as { capabilities?: Record<string, string> };
    return json(r, { role_type: "builder", capabilities: { ...BASE, ...(req.capabilities ?? {}) },
      vision_available: true, errors: [], warnings: [] });
  });
}

test("launch a one-shot and land on the run", async ({ page }) => {
  await mockCommon(page);
  let body: unknown = null;
  await page.route("**/api/oneshot", (r) => {
    body = r.request().postDataJSON();
    return json(r, { accepted: true, needs_approval: false, project_id: "oneshot-1",
      run_id: "run_1", job_id: "job_1" }, 202);
  });
  await page.route("**/api/runs/run_1**", (r) => json(r, {}, 404));

  await page.goto("/oneshot");
  await page.getByRole("button", { name: "Launch" }).click();
  await expect(page.getByText("Describe what you want done.")).toBeVisible();

  await page.getByTestId("example-cli").click();
  await expect(page.getByLabel("Goal")).toHaveValue("Build a CSV to JSON CLI");
  await page.getByLabel(/Budget/).fill("20");
  await page.getByRole("button", { name: "Launch" }).click();

  await expect(page).toHaveURL(/\/runs\/run_1/);
  expect(body).toEqual({ goal: "Build a CSV to JSON CLI", web: "researcher", budget: 20, demo: false });
});

test("approve a team after a capability change, with a playbook entry visible", async ({ page }) => {
  await mockCommon(page);
  await page.route("**/api/meta/propose", (r) =>
    json(r, {
      rationale: "A builder and a reviewer.",
      agents: [
        { role: "builder", description: "Writes code", depends_on: [], model_tier: "solid" },
        { role: "reviewer", description: "Reviews", depends_on: ["builder"], model_tier: "solid",
          is_reviewer: true, review_of: ["builder"], max_review_rounds: 3 },
      ],
      rbac: {},
      playbook_entries_used: ["e1"],
    }));
  await page.route("**/api/projects/p1/playbook", (r) =>
    json(r, { entries: [{ id: "e1", section: "brief_addenda", key: "builder",
      value: "Read research/notes.md first", confidence: 0.8, state: "active",
      evidence_runs: ["r1", "r2"] }], metrics: [] }));
  let saved: { agents: Array<{ role: string; capabilities?: Record<string, string> }> } | null = null;
  await page.route("**/api/projects/p1", (r) => {
    if (r.request().method() === "PUT") {
      saved = r.request().postDataJSON();
      return json(r, {});
    }
    return json(r, { project_id: "p1", goal: "g" });
  });

  await page.goto("/propose?project_id=p1");
  await page.getByPlaceholder(/e\.g\./).fill("Improve the CLI");
  await page.getByRole("button", { name: "Propose team" }).click();

  await expect(page.getByTestId("team-node-builder")).toBeVisible();
  await expect(page.getByText("review, max 3 rounds")).toBeVisible();
  const entry = page.getByTestId("playbook-entry");
  await expect(entry).toContainText("+ brief addendum for builder: Read research/notes.md first");
  await expect(entry).toContainText("confidence 80%");

  await page.getByLabel("Agent harness").selectOption("opencode");
  const cost = page.getByTestId("cost-strip").getByText(/per run/);
  await page.getByTestId("team-node-builder").click();
  const before = await cost.textContent();
  await page.getByLabel("builder web").selectOption("search+fetch");
  await expect(page.getByTestId("team-node-builder")).toContainText("web: search+fetch");
  await expect(cost).not.toHaveText(before ?? "");
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Approve & create" }).click();
  await expect.poll(() => saved?.agents.find((a) => a.role === "builder")?.capabilities?.web)
    .toBe("search+fetch");
});
