import { expect, test, type Page, type Route } from "@playwright/test";
import { REPORT_FIXTURE, SAMPLE_DIFF } from "../src/features/report/__fixtures__/report";

// The run report for a finished run against a mocked backend.

const RUN = REPORT_FIXTURE.run_id;

async function mockBackend(page: Page): Promise<void> {
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route(/\/api\/(runs|projects)\//, (route) => json(route, { detail: "not mocked" }, 404));
  await page.route(`**/api/runs/${RUN}/report`, (route) => json(route, REPORT_FIXTURE));
  await page.route(`**/api/runs/${RUN}/cards`, (route) => json(route, { cards: [] }));
  await page.route(`**/api/runs/${RUN}/git/diff?ref=*`, (route) =>
    json(route, { sha: "a".repeat(40), diff: SAMPLE_DIFF }),
  );
  await page.routeWebSocket(new RegExp(`/ws/runs/${RUN}$`), (ws) => {
    ws.send(JSON.stringify({ type: "snapshot", live: false, signals: [] }));
  });
}

test("the report shows costs, an intervention with its rationale, and a commit diff", async ({ page }) => {
  await mockBackend(page);
  await page.goto(`/runs/${RUN}/report`);

  await expect(page.getByTestId("run-report")).toBeVisible();
  await expect(page.getByTestId("section-summary")).toContainText("12m 03s");

  const rows = page.getByTestId("cost-row");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("dev");
  await expect(rows.first()).toContainText("120k");
  await expect(page.getByTestId("cost-table")).toContainText("$1.38");

  const iv = page.getByTestId("report-intervention").first();
  await expect(iv).toContainText("rebrief");
  await expect(iv).toContainText("dev kept guessing the response shape");
  await expect(iv).toContainText("worked");

  await page.getByRole("button", { name: /Show diff/ }).click();
  const diff = page.getByTestId("diff-viewer");
  await expect(diff).toBeVisible();
  await expect(diff).toContainText("print('extra')");
  await expect(diff.locator(".diff-code-insert").first()).toBeVisible();
});
