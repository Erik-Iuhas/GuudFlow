import { readFileSync } from "node:fs";
import { expect, test, type Page, type Route } from "@playwright/test";

// The run cockpit against a mocked backend: REST via page.route, both websockets via
// routeWebSocket, fed with the recorded 2,000-signal run.

interface Fixture {
  meta: { run_id: string; project_id: string };
  project: unknown;
  cards: unknown[];
  quips: unknown[];
  signals: { summary: string; type: string }[];
}

const fixture = JSON.parse(
  readFileSync(new URL("../src/features/cockpit/__fixtures__/run2000.json", import.meta.url), "utf8"),
) as Fixture;
const RUN = fixture.meta.run_id;

async function mockBackend(page: Page): Promise<void> {
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  // Only the backend namespaces: a bare **/api/** would also match Vite modules under /src/api/.
  await page.route(/\/api\/(runs|projects)\//, (route) => json(route, { detail: "not mocked" }, 404));
  await page.route(`**/api/runs/${RUN}`, (route) => json(route, fixture.meta));
  await page.route(`**/api/projects/${fixture.meta.project_id}`, (route) => json(route, fixture.project));
  await page.route(`**/api/runs/${RUN}/cards`, (route) => json(route, { cards: fixture.cards }));

  await page.routeWebSocket(new RegExp(`/ws/runs/${RUN}$`), (ws) => {
    ws.send(JSON.stringify({ type: "snapshot", live: false, signals: fixture.signals }));
  });
  await page.routeWebSocket(new RegExp(`/ws/runs/${RUN}/quips$`), (ws) => {
    ws.send(JSON.stringify({ type: "snapshot", live: false, quips: fixture.quips }));
  });
}

test("the cockpit renders the recorded run, windows the board and scrolls it", async ({ page }) => {
  await mockBackend(page);
  await page.goto(`/runs/${RUN}`);

  const cockpit = page.getByTestId("cockpit");
  await expect(cockpit).toBeVisible();
  await expect(page.getByTestId("run-status")).toHaveText("complete");
  await expect(cockpit).toContainText("2000 signals");

  // Graph, lanes, budget and the always-visible comment box.
  await expect(page.getByTestId("graph-node-planner")).toHaveAttribute("data-state", "complete");
  await expect(page.getByTestId("graph-node-overseer")).toBeVisible();
  await expect(page.getByText(/round \d\/3/).first()).toBeVisible();
  for (const role of ["planner", "backend", "frontend", "reviewer", "overseer"]) {
    await expect(page.getByRole("region", { name: role, exact: true })).toBeAttached();
  }
  await expect(page.getByTestId("budget-meter")).toContainText("/ $5.00");
  await expect(page.getByLabel("Comment for the agents")).toBeVisible();

  // The board is windowed: far fewer rows in the DOM than signals, newest in view first.
  const board = page.getByTestId("board-feed");
  await expect(board.getByText("run complete").first()).toBeVisible();
  const rows = await board.locator("[data-signal]").count();
  expect(rows).toBeGreaterThan(0);
  expect(rows).toBeLessThan(300);

  // Scroll to the top: the first signals appear and the newest are unmounted.
  await board.evaluate((el) => {
    el.scrollTop = 0;
  });
  await expect(board.getByText("planner started").first()).toBeVisible();
  await expect(board.getByText("run complete")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Jump to latest" })).toBeVisible();
  await page.getByRole("button", { name: "Jump to latest" }).click();
  await expect(board.getByText("run complete").first()).toBeVisible();

  // Tabs and shortcuts.
  await page.getByRole("tab", { name: /Overseer/ }).click();
  await expect(page.getByTestId("intervention").first()).toBeVisible();
  await page.keyboard.press("c");
  await expect(page.getByLabel("Comment for the agents")).toBeFocused();
});

test("below 1024px the graph is a strip and the panels open in a drawer", async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 900 });
  await mockBackend(page);
  await page.goto(`/runs/${RUN}`);

  await expect(page.getByTestId("cockpit")).toBeVisible();
  await expect(page.getByLabel("Run panels")).toHaveCount(0);
  await page.getByRole("button", { name: "Board and feeds" }).click();
  await expect(page.getByRole("tab", { name: "Board" })).toBeVisible();
  await expect(page.getByLabel("Comment for the agents")).toBeAttached();
});
