import { expect, test, type Page, type Route, type WebSocketRoute } from "@playwright/test";

// The board against a mocked backend: REST via page.route, the run socket via routeWebSocket.
// Add a card, drag one to reprioritise, and comment on one.

interface Card {
  id: string;
  title: string;
  owner: string;
  column: string;
  kind: string;
  size: string;
  priority: number;
  review_round: number;
  acceptance: string[];
  depends_on: string[];
  column_since: string | null;
  thread: unknown[];
}

const card = (id: string, priority: number, extra: Partial<Card> = {}): Card => ({
  id, title: `Task ${id}`, owner: "dev", column: "todo", kind: "code", size: "S", priority,
  review_round: 0, acceptance: [`${id} works`], depends_on: [], column_since: null, thread: [], ...extra,
});

let seq = 0;
const signal = (type: string, detail: unknown, summary = "") => ({
  id: `e2e-${++seq}`, type, summary, detail: typeof detail === "string" ? detail : JSON.stringify(detail),
  from_agent: "dev", from_user: false, priority: "normal", iteration: 0, seen_by: [],
  timestamp: new Date().toISOString(),
});

interface Mock {
  patches: Array<{ url: string; body: Record<string, unknown> }>;
  posts: Array<{ url: string; body: Record<string, unknown> }>;
  push: (sig: ReturnType<typeof signal>) => void;
}

async function mockBackend(page: Page, cards: Card[]): Promise<Mock> {
  const mock: Mock = { patches: [], posts: [], push: () => undefined };
  let socket: WebSocketRoute | null = null;
  mock.push = (sig) => socket?.send(JSON.stringify({ type: "signal", signal: sig }));

  await page.routeWebSocket(/\/ws\/runs\/r1$/, (ws) => {
    socket = ws;
    ws.send(JSON.stringify({ type: "snapshot", live: true, signals: [] }));
  });

  const reply = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/runs/r1/cards", async (route) => {
    const req = route.request();
    if (req.method() === "GET") return reply(route, { cards });
    const body = req.postDataJSON() as Record<string, unknown>;
    mock.posts.push({ url: req.url(), body });
    const created = card("T9", 0, { title: String(body["title"]), acceptance: body["acceptance"] as string[] });
    cards.push(created);
    mock.push(signal("TASK_ASSIGNED", "T9", `task:T9 → dev: ${created.title}`));
    return reply(route, created, 201);
  });

  await page.route("**/api/runs/r1/cards/*/comments", async (route) => {
    const req = route.request();
    const body = req.postDataJSON() as Record<string, unknown>;
    mock.posts.push({ url: req.url(), body });
    const taskId = req.url().split("/").slice(-2, -1)[0] as string;
    const entry = { task_id: taskId, kind: "human_comment", author: "human", text: body["text"], iteration: 0, data: {} };
    mock.push(signal("TASK_THREAD", entry));
    return reply(route, entry, 201);
  });

  await page.route(/\/api\/runs\/r1\/cards\/[^/]+$/, async (route) => {
    const req = route.request();
    const body = req.postDataJSON() as { priority: number };
    mock.patches.push({ url: req.url(), body });
    const id = req.url().split("/").pop() as string;
    const target = cards.find((c) => c.id === id);
    if (target) target.priority = body.priority;
    return reply(route, target ?? card(id, body.priority));
  });

  return mock;
}

test("add a card, drag it, and comment on it", async ({ page }) => {
  const mock = await mockBackend(page, [card("T1", 2), card("T2", 1), card("T3", 0)]);
  await page.goto("/runs/r1/board");

  const todo = page.getByRole("list", { name: "dev To do" });
  const order = () => todo.locator("[data-testid^=card-]").evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
  await expect(todo.getByTestId("card-T1")).toBeVisible();
  expect(await order()).toEqual(["card-T1", "card-T2", "card-T3"]);

  // Add a card.
  await page.getByRole("button", { name: "Add card" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Title").fill("Write the docs");
  await dialog.getByLabel("Acceptance item 1").fill("README covers install");
  await dialog.getByLabel("Kind").selectOption("docs");
  await expect(dialog.locator("option[value=L]")).toHaveJSProperty("disabled", true);
  await dialog.getByRole("button", { name: "Create card" }).click();
  await expect(dialog).toBeHidden();
  expect(mock.posts[0]?.body).toMatchObject({
    owner: "dev", title: "Write the docs", acceptance: ["README covers install"], kind: "docs", size: "S",
  });
  await expect(todo.getByTestId("card-T9")).toBeVisible();

  // Drag the new card (priority 0, bottom of the cell) to the top.
  const handle = page.getByRole("button", { name: "Drag T9 to reprioritise" });
  const target = todo.getByTestId("card-T1");
  const from = (await handle.boundingBox())!;
  const to = (await target.boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2 - 10, { steps: 4 });
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
  await page.mouse.up();

  await expect.poll(() => mock.patches.length).toBeGreaterThan(0);
  for (const p of mock.patches) expect(Object.keys(p.body)).toEqual(["priority"]);
  expect(mock.patches).toEqual([{ url: expect.stringMatching(/\/cards\/T9$/), body: { priority: 3 } }]);
  // Still in To do: a drop never changes the column.
  await expect(todo.getByTestId("card-T9")).toHaveAttribute("data-column", "todo");
  await expect.poll(order).toEqual(["card-T9", "card-T1", "card-T2", "card-T3"]);

  // Comment on a card.
  await todo.getByRole("button", { name: /Task T2/ }).click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("checkbox", { name: "T2 works" })).not.toBeChecked();
  await drawer.getByLabel("Comment").fill("please add a retry");
  await drawer.getByRole("button", { name: "Post comment" }).click();
  expect(mock.posts.at(-1)).toMatchObject({ url: expect.stringContaining("/cards/T2/comments"), body: { text: "please add a retry" } });
  const entry = drawer.getByRole("region", { name: "Thread" }).getByText("please add a retry");
  await expect(entry).toBeVisible();
  await expect(entry.locator("xpath=ancestor::li[1]")).toHaveAttribute("data-kind", "human_comment");
});
