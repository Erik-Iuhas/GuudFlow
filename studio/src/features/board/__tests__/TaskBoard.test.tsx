import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRunStore } from "../../../stores/runEvents";
import { card, echoPatch, json, mockBackend, renderBoard, signal, thread } from "./helpers";

beforeEach(() => {
  localStorage.setItem("guudflow.token", "tok");
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const cell = (name: string) => screen.getByRole("list", { name });

const DIFF = [
  "diff --git a/src/app.ts b/src/app.ts",
  "index 1111111..2222222 100644",
  "--- a/src/app.ts",
  "+++ b/src/app.ts",
  "@@ -1 +1 @@",
  "-removed line",
  "+added line",
  "",
].join("\n");

describe("TaskBoard layout", () => {
  it("puts cards in the right swimlane and column, with counts and the Review cap", async () => {
    mockBackend([
      card({ id: "T1", owner: "dev", column: "in_progress" }),
      card({ id: "T2", owner: "dev", column: "review", review_round: 1 }),
      card({ id: "T3", owner: "tester", column: "review" }),
      card({ id: "T4", owner: "tester", column: "done" }),
    ]);
    renderBoard({ reviewCap: 2 });
    await screen.findByTestId("card-T1");

    expect(within(cell("dev In progress")).getByTestId("card-T1")).toBeInTheDocument();
    expect(within(cell("dev Review")).getByTestId("card-T2")).toBeInTheDocument();
    expect(within(cell("tester Review")).getByTestId("card-T3")).toBeInTheDocument();
    expect(within(cell("tester Done")).getByTestId("card-T4")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Lane dev" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Lane tester" })).toBeInTheDocument();

    expect(within(screen.getByTestId("col-in_progress")).getByLabelText("1 cards")).toBeInTheDocument();
    const review = screen.getByTestId("col-review");
    expect(within(review).getByText("2/2")).toBeInTheDocument();
    expect(within(review).getByText("at cap")).toBeInTheDocument();
    expect(within(screen.getByTestId("card-T2")).getByText("round 1")).toBeInTheDocument();
  });

  it("shows owner, kind and size badges, age in column, and a red ring for needs_attention", async () => {
    const since = new Date(Date.now() - 5 * 60_000).toISOString();
    mockBackend([
      card({ id: "T1", column: "needs_attention", kind: "test", size: "M", column_since: since }),
    ]);
    renderBoard();
    const tile = await screen.findByTestId("card-T1");
    expect(within(tile).getByTestId("owner-T1")).toHaveTextContent("dev");
    expect(within(tile).getByText("test")).toBeInTheDocument();
    expect(within(tile).getByText("M")).toBeInTheDocument();
    expect(within(tile).getByText(/5m in column/)).toBeInTheDocument();
    expect(tile.className).toContain("ring-destructive");
  });

  it("shows a blocked-on chip from the thread", async () => {
    mockBackend([
      card({
        id: "T1", column: "blocked",
        thread: [thread("T1", "blocked_on", "waiting on ops: need a db", "dev", { role: "ops", unblock_card: "T9" })],
      }),
    ]);
    renderBoard();
    const tile = await screen.findByTestId("card-T1");
    expect(within(tile).getByText("blocked on ops T9")).toBeInTheDocument();
  });

  it("moves a card when a TASK_MOVED signal arrives", async () => {
    mockBackend([card({ id: "T1", owner: "dev", column: "todo" })]);
    const { store } = renderBoard();
    await screen.findByTestId("card-T1");
    expect(within(cell("dev To do")).getByTestId("card-T1")).toBeInTheDocument();

    act(() => {
      store.getState().ingest(
        signal("TASK_MOVED", { task_id: "T1", from: "todo", to: "in_progress", reason: "turn started", owner: "dev", round: 0 }),
      );
    });
    expect(within(cell("dev In progress")).getByTestId("card-T1")).toBeInTheDocument();
    expect(within(cell("dev To do")).queryByTestId("card-T1")).toBeNull();
  });

  it("shows the empty state when there are no cards", async () => {
    mockBackend([]);
    renderBoard();
    expect(await screen.findByText("The planner hasn't produced cards yet")).toBeInTheDocument();
  });

  it("shows an error state with a retry", async () => {
    let fail = true;
    mockBackend([card({ id: "T1" })], (c) =>
      fail && c.method === "GET" ? json({ detail: "boom" }, 500) : undefined,
    );
    renderBoard();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not load the cards");
    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("card-T1")).toBeInTheDocument();
  });

  it("disables Add card and reprioritising once the run is no longer live", async () => {
    mockBackend([card({ id: "T1", priority: 1 }), card({ id: "T2" })]);
    const store = createRunStore("r1");
    store.getState().setLive(false);
    renderBoard({ store });
    await screen.findByTestId("card-T1");
    expect(screen.getByRole("button", { name: "Add card" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Raise priority/ })).toBeNull();
  });
});

describe("reprioritising", () => {
  const cards = () => [
    card({ id: "T1", owner: "dev", priority: 2 }),
    card({ id: "T2", owner: "dev", priority: 1 }),
    card({ id: "T3", owner: "dev", priority: 0 }),
  ];
  const order = () =>
    within(cell("dev To do"))
      .getAllByTestId(/^card-/)
      .map((n) => n.getAttribute("data-testid"));

  it("orders a cell by priority, highest first", async () => {
    mockBackend(cards());
    renderBoard();
    await screen.findByTestId("card-T1");
    expect(order()).toEqual(["card-T1", "card-T2", "card-T3"]);
  });

  it("raising a card PATCHes the changed priorities with bearer auth and never changes the column", async () => {
    const backend = mockBackend(cards(), echoPatch);
    renderBoard();
    await screen.findByTestId("card-T3");
    fireEvent.click(screen.getByRole("button", { name: "Raise priority of T3" }));
    // Optimistic: the card jumps before any reply.
    await waitFor(() => expect(order()).toEqual(["card-T1", "card-T3", "card-T2"]));
    // T1 (2) and T2 (1) leave no integer gap for T3, so the cell is renumbered 2/1/0.
    await waitFor(() => expect(backend.writes()).toHaveLength(2));
    const byUrl = Object.fromEntries(backend.writes().map((c) => [c.url, c]));
    expect(byUrl["/api/runs/r1/cards/T3"]).toMatchObject({
      method: "PATCH", body: { priority: 1 }, auth: "Bearer tok",
    });
    expect(byUrl["/api/runs/r1/cards/T2"]).toMatchObject({ method: "PATCH", body: { priority: 0 } });
    for (const call of backend.writes()) expect(call.body).not.toHaveProperty("column");
    expect(screen.getByTestId("card-T3")).toHaveAttribute("data-column", "todo");
    expect(order()).toEqual(["card-T1", "card-T3", "card-T2"]);
  });

  it("moving a card to the top sends a single PATCH one priority above the old top", async () => {
    const backend = mockBackend(cards(), echoPatch);
    renderBoard();
    await screen.findByTestId("card-T2");
    fireEvent.click(screen.getByRole("button", { name: "Raise priority of T2" }));
    await waitFor(() => expect(backend.writes()).toHaveLength(1));
    expect(backend.writes()[0]).toMatchObject({ url: "/api/runs/r1/cards/T2", body: { priority: 3 } });
    await waitFor(() => expect(order()).toEqual(["card-T2", "card-T1", "card-T3"]));
  });

  it("rolls back the optimistic order and shows an error when the PATCH fails", async () => {
    mockBackend(cards(), (c) => (c.method === "PATCH" ? json({ detail: "T3 is done" }, 400) : undefined));
    renderBoard();
    await screen.findByTestId("card-T3");
    fireEvent.click(screen.getByRole("button", { name: "Lower priority of T1" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Could not change priority");
    await waitFor(() => expect(order()).toEqual(["card-T1", "card-T2", "card-T3"]));
  });

  it("done cards cannot be reprioritised", async () => {
    mockBackend([card({ id: "T1", column: "done" }), card({ id: "T2", column: "done" })]);
    renderBoard();
    await screen.findByTestId("card-T1");
    expect(screen.queryByRole("button", { name: /Raise priority/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Drag T1/ })).toBeNull();
  });

  it("keeps the new priority from the PATCH reply after the round trip", async () => {
    mockBackend(cards(), echoPatch);
    const { store } = renderBoard();
    await screen.findByTestId("card-T1");
    fireEvent.click(screen.getByRole("button", { name: "Lower priority of T1" }));
    await waitFor(() => expect(store.getState().cards["T1"]?.priority).toBe(1));
    expect(store.getState().cards["T1"]?.column).toBe("todo");
  });
});

describe("CardDrawer", () => {
  const withThread = () =>
    card({
      id: "T1",
      title: "Build the API client",
      acceptance: ["models exist", "tests pass"],
      thread: [
        thread("T1", "note", "started work", "dev"),
        thread("T1", "human_comment", "please use httpx", "human"),
        thread("T1", "review_issue", "tests pass: no tests", "reviewer", { item: "tests pass" }),
        thread("T1", "note", "merged aaaa1111..bbbb2222", "dev", { merge: ["aaaa1111aaaa", "bbbb2222bbbb"] }),
      ],
    });

  it("shows the thread, the checklist met state, merge ranges and related artifacts", async () => {
    mockBackend([withThread()], (c) =>
      c.url.includes("/git/diff") ? json({ sha: "bbbb2222bbbb", diff: DIFF }) : undefined,
    );
    const { store } = renderBoard();
    await screen.findByTestId("card-T1");
    act(() => {
      store.getState().ingest([
        signal("AGENT_RESULT", {
          result: { task_id: "T1", acceptance: [{ item: "models exist", met: true, evidence: "pytest -k models passes" }] },
        }),
        signal("ARTIFACT", { path: "docs/api.md", kind: "doc", role: "dev", task_id: "T1" }),
        signal("ARTIFACT", { path: "other.md", kind: "doc", role: "dev", task_id: "T2" }),
      ]);
    });

    fireEvent.click(screen.getByRole("button", { name: /Build the API client/ }));
    const drawer = await screen.findByRole("dialog");

    expect(within(drawer).getByRole("checkbox", { name: "models exist" })).toBeChecked();
    expect(within(drawer).getByRole("checkbox", { name: "tests pass" })).not.toBeChecked();

    const entries = within(drawer).getByRole("region", { name: "Thread" });
    expect(within(entries).getByText("started work")).toBeInTheDocument();
    const human = within(entries).getByText("please use httpx").closest("li");
    expect(human).toHaveAttribute("data-kind", "human_comment");
    expect(human?.className).toContain("bg-accent");
    expect(within(entries).getByText("Review issue")).toBeInTheDocument();

    const diffs = within(drawer).getByRole("region", { name: "Diffs" });
    expect(within(diffs).getByText("aaaa1111..bbbb2222")).toBeInTheDocument();
    fireEvent.click(within(diffs).getByRole("button", { name: "View diff" }));
    const viewer = await within(diffs).findByTestId("diff-viewer");
    expect(within(viewer).getByRole("region", { name: "src/app.ts" })).toBeInTheDocument();
    expect(viewer).toHaveTextContent("added line");

    const artifacts = within(drawer).getByRole("region", { name: "Artifacts" });
    expect(within(artifacts).getByText("docs/api.md")).toBeInTheDocument();
    expect(within(artifacts).queryByText("other.md")).toBeNull();
  });

  it("posts a comment with the right payload and clears the box", async () => {
    const backend = mockBackend([withThread()], (c) =>
      c.method === "POST" ? json(thread("T1", "human_comment", "ship it", "human")) : undefined,
    );
    renderBoard();
    await screen.findByTestId("card-T1");
    fireEvent.click(screen.getByRole("button", { name: /Build the API client/ }));
    const drawer = await screen.findByRole("dialog");
    const box = within(drawer).getByLabelText("Comment");
    const post = within(drawer).getByRole("button", { name: "Post comment" });
    expect(post).toBeDisabled();
    fireEvent.change(box, { target: { value: "  ship it  " } });
    fireEvent.click(post);
    await waitFor(() => expect(backend.writes()).toHaveLength(1));
    expect(backend.writes()[0]).toMatchObject({
      method: "POST", url: "/api/runs/r1/cards/T1/comments", body: { text: "ship it" }, auth: "Bearer tok",
    });
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("shows a comment error without clearing the text", async () => {
    mockBackend([withThread()], (c) => (c.method === "POST" ? json({ detail: "run 'r1' is not active" }, 404) : undefined));
    renderBoard();
    await screen.findByTestId("card-T1");
    fireEvent.click(screen.getByRole("button", { name: /Build the API client/ }));
    const drawer = await screen.findByRole("dialog");
    fireEvent.change(within(drawer).getByLabelText("Comment"), { target: { value: "hello" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "Post comment" }));
    expect(await within(drawer).findByRole("alert")).toHaveTextContent("not active");
    expect(within(drawer).getByLabelText("Comment")).toHaveValue("hello");
  });
});

describe("AddCardDialog", () => {
  it("posts the card with the right payload and disables size L", async () => {
    const backend = mockBackend([card({ id: "T1", owner: "dev" }), card({ id: "T2", owner: "tester" })], (c) =>
      c.method === "POST" ? json(card({ id: "T3", owner: "tester", title: "Write the docs", kind: "docs" }), 201) : undefined,
    );
    renderBoard();
    await screen.findByTestId("card-T1");
    fireEvent.click(screen.getByRole("button", { name: "Add card" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByRole("option", { name: /^L/ })).toBeDisabled();
    expect(within(dialog).getByText(/Size L is not allowed/)).toBeInTheDocument();
    expect(within(dialog).getAllByRole("option", { name: "dev" })).toHaveLength(1);

    fireEvent.change(within(dialog).getByLabelText("Owner"), { target: { value: "tester" } });
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Write the docs" } });
    fireEvent.change(within(dialog).getByLabelText("Acceptance item 1"), { target: { value: "README covers install" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add item" }));
    fireEvent.change(within(dialog).getByLabelText("Acceptance item 2"), { target: { value: " docs build " } });
    fireEvent.change(within(dialog).getByLabelText("Kind"), { target: { value: "docs" } });
    fireEvent.change(within(dialog).getByLabelText("Size"), { target: { value: "M" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create card" }));

    await waitFor(() => expect(backend.writes()).toHaveLength(1));
    expect(backend.writes()[0]).toMatchObject({
      method: "POST",
      url: "/api/runs/r1/cards",
      auth: "Bearer tok",
      body: {
        owner: "tester", title: "Write the docs", acceptance: ["README covers install", "docs build"],
        kind: "docs", size: "M",
      },
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByTestId("card-T3")).toHaveAttribute("data-column", "todo");
  });

  it("validates client side and surfaces a server error", async () => {
    const backend = mockBackend([card({ id: "T1", owner: "dev" })], (c) =>
      c.method === "POST" ? json({ detail: "unknown owner 'dev'" }, 400) : undefined,
    );
    renderBoard();
    await screen.findByTestId("card-T1");
    fireEvent.click(screen.getByRole("button", { name: "Add card" }));
    const dialog = await screen.findByRole("dialog");

    fireEvent.click(within(dialog).getByRole("button", { name: "Create card" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("at least 5 characters");
    expect(backend.writes()).toHaveLength(0);

    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "A real title" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create card" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("at least one acceptance item");

    fireEvent.change(within(dialog).getByLabelText("Acceptance item 1"), { target: { value: "it works" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create card" }));
    await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent("unknown owner"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
