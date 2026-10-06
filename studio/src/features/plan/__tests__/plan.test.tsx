import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { DraftChangeWire, DraftWire, PlanMessageWire, SpecWire } from "../api";
import { PlanTab } from "../PlanTab";
import { approveBlocker, draftCounts, groupChanges } from "../model";

const spec: SpecWire = {
  version: 3,
  requirements: [
    {
      id: "R1", text: "Users can sign in", kind: "functional", priority: "must", assumed: true, experiment: false,
      status: "active", source: "human", version: 1,
      checks: [
        { id: "R1.1", text: "valid login works", state: "met", run_id: "r1" },
        { id: "R1.2", text: "bad login is refused", state: "unknown", run_id: null },
        { id: "R1.3", text: "lockout after 5 tries", state: "unmet", run_id: "r1" },
      ],
    },
  ],
  decisions: [
    { id: "D1", title: "Storage", choice: "SQLite", reason: "Simple and local enough", serves: ["R1"], binding: true,
      status: "accepted", decided_by: "pm", supersedes: null, version: 1 },
  ],
  rules: [{ id: "rule-1", text: "No new runtime deps", source: "D1", applies_to: ["*"], strength: "binding", status: "active" }],
};

function dc(cid: string, change: DraftChangeWire["change"], accepted: boolean | null = null, by = "pm"): DraftChangeWire {
  return { cid, change, accepted, by, touched: false };
}

function draftOf(changes: DraftChangeWire[]): DraftWire {
  return { id: "draft-2", base_version: 3, status: "open", changes };
}

const msg = (id: number, author: "human" | "pm", text: string, over: Partial<PlanMessageWire> = {}): PlanMessageWire => ({
  id, at: 1_700_000_000, author, text, draft_id: null, mode: "update", ...over,
});

interface Server {
  plan: { messages: PlanMessageWire[]; draft: DraftWire | null };
  calls: { method: string; path: string; body: unknown }[];
  handlers: Record<string, (body: unknown) => { status?: number; json: unknown }>;
}

let server: Server;

beforeEach(() => {
  server = { plan: { messages: [], draft: null }, calls: [], handlers: {} };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      server.calls.push({ method, path, body });
      const h = server.handlers[`${method} ${path}`];
      let out: { status?: number; json: unknown };
      if (h) out = h(body);
      else if (method === "GET" && path.endsWith("/spec")) out = { json: spec };
      else if (method === "GET" && path.endsWith("/plan")) out = { json: server.plan };
      else out = { status: 404, json: { detail: `no handler for ${method} ${path}` } };
      const status = out.status ?? 200;
      return { ok: status < 400, status, statusText: "x", json: async () => out.json } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const spy = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <PlanTab projectId="p1" />
    </QueryClientProvider>,
  );
  return { qc, spy };
}

const mutating = () => server.calls.filter((c) => c.method !== "GET");

describe("plan model", () => {
  it("counts, groups by section and explains a blocked approve", () => {
    const d = draftOf([
      dc("c1", { op: "add_item", ref: "new:1", title: "Login page" }, true),
      dc("c2", { op: "drop_requirement", id: "R1", reason: "out of scope" }, false),
      dc("c3", { op: "add_rule", text: "Be kind" }),
    ]);
    expect(draftCounts(d)).toEqual({ total: 3, accepted: 1, rejected: 1, undecided: 1 });
    expect(groupChanges(d, spec).map((g) => g.section)).toEqual(["Requirements", "Rules", "Work items"]);
    expect(approveBlocker(draftCounts(d))).toMatch(/1 change is undecided/);
  });
});

describe("PlanTab conversation", () => {
  it("shows the empty state and starts the first draft", async () => {
    const turn = { messages: [msg(1, "pm", "Here is draft one.\nSecond line", { draft_id: "draft-1" })], draft: null };
    server.handlers["POST /api/projects/p1/plan/start"] = () => ({ json: turn });
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Draft the first plan" }));
    expect(await screen.findByText(/Here is draft one/)).toBeInTheDocument();
    expect(screen.getByText("Jack")).toBeInTheDocument();
    expect(screen.getByText("project manager")).toBeInTheDocument();
    expect(screen.getByText("J")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Draft the first plan" })).toBeNull();
    expect(mutating()[0].path).toBe("/api/projects/p1/plan/start");
  });

  it("sends a message, shows the pending state and clears the composer", async () => {
    server.plan.messages = [msg(1, "pm", "Hello")];
    let release: (v: unknown) => void = () => undefined;
    const gate = new Promise((r) => (release = r));
    const orig = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "POST" && path.endsWith("/plan/messages")) {
        server.calls.push({ method: "POST", path, body: JSON.parse(String(init.body)) });
        await gate;
        return { ok: true, status: 200, statusText: "", json: async () => ({ messages: [msg(2, "human", "Use SQLite"), msg(3, "pm", "Done")], draft: null }) } as Response;
      }
      return orig(path, init);
    }));
    renderTab();
    const box = await screen.findByLabelText(/Answer, push back/);
    expect(box).toHaveAttribute("rows", "6");
    fireEvent.change(box, { target: { value: "Use SQLite" } });
    fireEvent.click(screen.getByRole("button", { name: "Just discuss" }));
    expect(screen.getByRole("button", { name: "Just discuss" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    expect(await screen.findByText("Jack is drafting…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    release(null);
    expect(await screen.findByText("Done")).toBeInTheDocument();
    expect(screen.queryByText("Jack is drafting…")).toBeNull();
    expect((screen.getByLabelText(/Answer, push back/) as HTMLTextAreaElement).value).toBe("");
    expect(mutating()[0].body).toEqual({ text: "Use SQLite", mode: "discuss" });
  });

  it("shows the server's detail on error and keeps the text", async () => {
    server.plan.messages = [msg(1, "pm", "Hello")];
    server.handlers["POST /api/projects/p1/plan/messages"] = () => ({ status: 502, json: { detail: "model is down" } });
    renderTab();
    const box = await screen.findByLabelText(/Answer, push back/);
    fireEvent.change(box, { target: { value: "my long answer" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("model is down");
    expect((screen.getByLabelText(/Answer, push back/) as HTMLTextAreaElement).value).toBe("my long answer");
  });

  it("renders message text as plain text", async () => {
    server.plan.messages = [msg(1, "pm", "<b>bold</b>")];
    renderTab();
    expect(await screen.findByText("<b>bold</b>")).toBeInTheDocument();
    expect(document.querySelector("b")).toBeNull();
  });
});

describe("PlanTab draft", () => {
  const changes = (): DraftChangeWire[] => [
    dc("c1", { op: "edit_requirement", id: "R1", text: "Users can sign in with a passkey" }),
    dc("c2", { op: "add_requirement", ref: "new:1", text: "Export to CSV", kind: "functional", priority: "should",
      checks: ["file opens in Excel", "has a header row"], assumed: true, experiment: true }),
    dc("c3", { op: "add_decision", ref: "new:2", title: "Format", choice: "CSV", reason: "Everyone can open it", binding: true }, true),
    dc("c4", { op: "add_item", ref: "new:3", title: "Export button", serves: ["R1"], size: "S", depends_on: ["new:4"] }),
  ];

  it("groups changes, strikes through the old text and shows the details", async () => {
    server.plan = { messages: [msg(1, "pm", "Draft")], draft: draftOf(changes()) };
    renderTab();
    await screen.findByText("Spec · draft");
    expect(screen.getByText("4 changes · 1 accepted · 3 undecided")).toBeInTheDocument();
    for (const name of ["Requirements", "Decisions", "Work items"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    const old = screen.getByText("Users can sign in");
    expect(old.tagName).toBe("DEL");
    const add = screen.getByTestId("change-c2");
    expect(within(add).getByText("file opens in Excel")).toBeInTheDocument();
    expect(within(add).getByText("assumed")).toBeInTheDocument();
    expect(within(add).getByText("experiment")).toBeInTheDocument();
    expect(within(screen.getByTestId("change-c3")).getByText("decided by the project manager")).toBeInTheDocument();
    expect(within(screen.getByTestId("change-c4")).getByText(/serves R1 · size S · after new 4/)).toBeInTheDocument();
  });

  it("ticks, unticks by clicking again, and rejects through PATCH", async () => {
    server.plan = { messages: [msg(1, "pm", "Draft")], draft: draftOf(changes()) };
    server.handlers["PATCH /api/projects/p1/plan/draft/c1"] = (body) =>
      ({ json: draftOf(changes().map((c) => (c.cid === "c1" ? { ...c, accepted: (body as { accepted: boolean | null }).accepted } : c))) });
    renderTab();
    const accept = await screen.findByRole("button", { name: "Accept R1" });
    expect(accept).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(accept);
    await waitFor(() => expect(screen.getByRole("button", { name: "Accept R1" })).toHaveAttribute("aria-pressed", "true"));
    expect(mutating()[0]).toMatchObject({ method: "PATCH", body: { accepted: true } });
    fireEvent.click(screen.getByRole("button", { name: "Accept R1" }));
    await waitFor(() => expect(mutating()).toHaveLength(2));
    expect(mutating()[1].body).toEqual({ accepted: null });
    fireEvent.click(screen.getByRole("button", { name: "Reject R1" }));
    await waitFor(() => expect(mutating()).toHaveLength(3));
    expect(mutating()[2].body).toEqual({ accepted: false });
  });

  it("keeps Approve disabled while undecided, then approves and invalidates", async () => {
    const open = draftOf(changes());
    server.plan = { messages: [msg(1, "pm", "Draft")], draft: open };
    server.handlers["POST /api/projects/p1/plan/draft/approve"] = () => {
      server.plan = { ...server.plan, draft: null };
      return { json: { version: 4, applied: ["c1"], created: { "new:1": "R2" } } };
    };
    const { spy } = renderTab();
    const approve = await screen.findByRole("button", { name: "Approve draft" });
    expect(approve).toBeDisabled();
    expect(approve.parentElement).toHaveAttribute("title", expect.stringMatching(/3 changes are undecided/));
    cleanup();

    server.plan = { messages: [msg(1, "pm", "Draft")], draft: draftOf(changes().map((c) => ({ ...c, accepted: c.cid === "c4" ? false : true }))) };
    const r = renderTab();
    const ok = await screen.findByRole("button", { name: "Approve draft" });
    expect(ok).toBeEnabled();
    fireEvent.click(ok);
    expect(await screen.findByText("Approved: spec v4")).toBeInTheDocument();
    const keys = r.spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toEqual(expect.arrayContaining([
      JSON.stringify(["spec", "p1"]), JSON.stringify(["plan", "p1"]), JSON.stringify(["projects", "p1", "backlog"]),
    ]));
    expect(spy).not.toHaveBeenCalled();
  });

  it("discards the draft", async () => {
    server.plan = { messages: [msg(1, "pm", "Draft")], draft: draftOf(changes()) };
    server.handlers["POST /api/projects/p1/plan/draft/discard"] = () => {
      server.plan = { ...server.plan, draft: null };
      return { json: {} };
    };
    renderTab();
    fireEvent.click(await screen.findByRole("button", { name: "Discard draft" }));
    expect(await screen.findByText("Version 3")).toBeInTheDocument();
    expect(mutating()[0].path).toBe("/api/projects/p1/plan/draft/discard");
  });
});

describe("SpecView", () => {
  it("shows counts, check states, tags and decisions", async () => {
    server.plan = { messages: [msg(1, "pm", "Hi")], draft: null };
    renderTab();
    expect(await screen.findByText("Requirements (1)")).toBeInTheDocument();
    expect(screen.getByText("Decisions (1)")).toBeInTheDocument();
    expect(screen.getByText("Rules (1)")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "met" })).toHaveTextContent("✓");
    expect(screen.getByRole("img", { name: "unknown" })).toHaveTextContent("○");
    expect(screen.getByRole("img", { name: "unmet" })).toHaveTextContent("✕");
    expect(screen.getByText("assumed")).toBeInTheDocument();
    expect(screen.getByText("binding")).toBeInTheDocument();
    expect(screen.getByText("decided by Jack")).toBeInTheDocument();
    expect(screen.getByText("No new runtime deps")).toBeInTheDocument();
  });

  it("can switch between the draft and the spec", async () => {
    server.plan = { messages: [msg(1, "pm", "Hi")], draft: draftOf([dc("c1", { op: "add_rule", text: "Be kind" })]) };
    renderTab();
    await screen.findByText("Spec · draft");
    fireEvent.click(screen.getByRole("button", { name: "Spec" }));
    expect(await screen.findByText("Version 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Draft" }));
    expect(await screen.findByText("Spec · draft")).toBeInTheDocument();
  });
});
