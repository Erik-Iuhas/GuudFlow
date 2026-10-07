import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import type { BacklogItemWire, RunBranchWire } from "../api";
import type { SpecWire } from "../../plan/api";
import type { SpecSlice } from "../../spec/api";
import { detailKey } from "../../project/useDetailLevel";
import { FollowPanel } from "../../cockpit/FollowPanel";
import { WorkOverview } from "../WorkOverview";
import { WorkTechnical } from "../WorkTechnical";
import { ProjectWork } from "../ProjectWork";
import { routedToYou } from "../overviewModel";

type Handler = (body: unknown) => { status?: number; json: unknown };
let handlers: Record<string, Handler>;
let calls: { method: string; path: string; body: unknown }[];

beforeEach(() => {
  handlers = {};
  calls = [];
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    const h = handlers[`${method} ${path.split("?")[0]}`];
    const out = h ? h(body) : { status: 404, json: { detail: `no handler for ${method} ${path}` } };
    const status = out.status ?? 200;
    return { ok: status < 400, status, statusText: "x", json: async () => out.json } as Response;
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function wrap(ui: ReactNode, route = "/projects/p1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function item(id: string, over: Partial<BacklogItemWire> = {}): BacklogItemWire {
  return {
    id, title: `Item ${id}`, acceptance: [], kind: "code", size: "M", priority: 0, status: "open",
    notes: "", files_hint: [], source: "human", created_at: 0, updated_at: 0, run_id: null,
    pending: null, history: [], depends_on: [], ...over,
  };
}

function run(id: string, over: Partial<RunBranchWire> = {}): RunBranchWire {
  return {
    run_id: id, state: "pending", base: "a", head: "b", commits: 2, files: ["app.py"],
    submitted_at: 1, decided_at: null, merged: "", note: "", parent_run: null, ...over,
  };
}

const NOW = Date.now() / 1000;
const spec: SpecWire & { requirements: { history?: unknown[] }[] } = {
  version: 2,
  requirements: [{
    id: "R4", text: "Login", kind: "functional", priority: "must", assumed: false, experiment: false,
    status: "active", source: "human", version: 1,
    checks: [
      { id: "R4.1", text: "valid login", state: "met", run_id: "r1" },
      { id: "R4.2", text: "bad login refused", state: "unmet", run_id: "r1" },
      { id: "R4.3", text: "lockout", state: "unknown", run_id: null },
    ],
    history: [{ at: NOW - 3600, version: 2, draft: "draft-4" }, { at: NOW - 30 * 86400, version: 1, draft: "draft-1" }],
  }],
  decisions: [],
  rules: [],
};

const items = [
  item("B1", { title: "Sign-in page", size: "S" }),
  item("B2", { title: "Reports", status: "in_run", run_id: "r9" }),
  item("B3", { title: "Search", status: "in_run", run_id: "r8", pending: "done" }),
  ...["B4", "B5", "B6", "B7", "B8", "B9"].map((id, n) =>
    item(id, { title: `Done ${id}`, status: "done", updated_at: n, history: [{ summary: `shipped ${id}` }] })),
];

describe("WorkOverview", () => {
  it("fills the four lanes from the backlog with plain sub-text and no ids", async () => {
    handlers["GET /api/projects/p1/spec"] = () => ({ json: spec });
    handlers["GET /api/projects/p1/plan"] = () => ({ json: { messages: [], draft: null } });
    wrap(<WorkOverview projectId="p1" items={items} pendingRuns={[run("r8")]} renderReview={() => null} />);
    const lane = (name: string) => within(screen.getByRole("region", { name }));
    expect(lane("Up next").getByText("Sign-in page")).toBeInTheDocument();
    expect(lane("Up next").getByText("Small job")).toBeInTheDocument();
    expect(lane("In a session").getByText("Reports")).toBeInTheDocument();
    expect(lane("Waiting for you").getByText("Search")).toBeInTheDocument();
    expect(lane("Done").getAllByRole("listitem")).toHaveLength(4);
    expect(lane("Done").getByText("+2 more")).toBeInTheDocument();
    expect(lane("Done").getByText("shipped B9")).toBeInTheDocument();
    expect(screen.queryByText("B1")).toBeNull();
    // Needs you: the waiting session, and the spec changes of the last week only.
    expect(within(screen.getByTestId("needs-you")).getByRole("button", { name: "Review and accept" })).toBeInTheDocument();
    // One short line per change, never the old text: id, title, draft and new version.
    expect(await screen.findByText(/R4 ".*": changed in draft-4 \(now v3\)/)).toBeInTheDocument();
    expect(screen.queryByText(/draft-1/)).toBeNull();
  });

  it("sends the ask box to the plan as an update, then opens the Plan tab", async () => {
    handlers["GET /api/projects/p1/spec"] = () => ({ json: spec });
    handlers["GET /api/projects/p1/plan"] = () => ({ json: { messages: [], draft: null } });
    handlers["POST /api/projects/p1/plan/messages"] = () => ({ json: { messages: [], draft: null } });
    wrap(<WorkOverview projectId="p1" items={[]} pendingRuns={[]} renderReview={() => null} />);
    fireEvent.change(screen.getByLabelText("Message the plan"), { target: { value: "Add dark mode" } });
    fireEvent.click(screen.getByRole("button", { name: "Send to the plan" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/projects/p1/plan/messages")).toBe(true));
    expect(calls.find((c) => c.path === "/api/projects/p1/plan/messages")!.body).toEqual({ text: "Add dark mode", mode: "update" });
    await waitFor(() => expect(screen.getByLabelText("Message the plan")).toHaveValue(""));
  });
});

describe("WorkTechnical", () => {
  beforeEach(() => {
    handlers["GET /api/projects/p1/spec"] = () => ({ json: spec });
    handlers["GET /api/projects/p1/plan"] = () => ({ json: { messages: [], draft: null } });
  });

  it("shows each R#.# acceptance line with its check state", async () => {
    const withChecks = item("B1", {
      title: "Sign-in", serves: ["R4"], total_cost_usd: 0.5,
      acceptance: ["R4.1: valid login", "R4.2: bad login refused", "R4.3: lockout", "plain line"],
    });
    wrap(<WorkTechnical projectId="p1" items={[withChecks]} pendingRuns={[]} />);
    expect(await screen.findByLabelText("R4.1 met")).toHaveTextContent("✓");
    expect(screen.getByLabelText("R4.2 unmet")).toHaveTextContent("✕");
    expect(screen.getByLabelText("R4.3 unknown")).toHaveTextContent("○");
    const row = within(screen.getByTestId("row-B1"));
    expect(row.getByText("R4")).toBeInTheDocument();
    expect(row.getByText("1/3 checks · $0.50")).toBeInTheDocument();
    expect(row.getByText("plain line")).toBeInTheDocument();
  });

  it("routes work to the seats the user holds; Owner always shows", async () => {
    const pending = [run("r1", { needs_cosign: true })];
    const { unmount } = wrap(<WorkTechnical projectId="p1" items={[]} pendingRuns={pending} seats={{ lead: true, reviewer: "all" }} />);
    const box = within(await screen.findByTestId("routed-to-you"));
    expect(box.getByRole("region", { name: "Reviewer" })).toHaveTextContent("Co-sign the code of r1");
    expect(box.getByRole("region", { name: "Lead" })).toBeInTheDocument();
    expect(box.getByRole("region", { name: "Owner" })).toHaveTextContent("Accept or reject r1");
    unmount();
    wrap(<WorkTechnical projectId="p1" items={[]} pendingRuns={pending} seats={{ lead: false, reviewer: "off" }} />);
    const solo = within(await screen.findByTestId("routed-to-you"));
    expect(solo.queryByRole("region", { name: "Reviewer" })).toBeNull();
    expect(solo.queryByRole("region", { name: "Lead" })).toBeNull();
    expect(solo.getByRole("region", { name: "Owner" })).toBeInTheDocument();
  });

  it("counts undecided draft decisions for the lead", () => {
    const plan = {
      messages: [],
      draft: { id: "d", base_version: 1, status: "open" as const, changes: [
        { cid: "c1", change: { op: "add_decision" }, accepted: null, by: "pm", touched: false },
        { cid: "c2", change: { op: "add_decision" }, accepted: true, by: "pm", touched: false },
      ] },
    };
    const r = routedToYou({ lead: true, reviewer: "off" }, [], plan);
    expect(r.lead).toEqual(["1 draft decision undecided"]);
    expect(r.reviewer).toBeNull();
    expect(r.owner).toContain("A plan draft waits for your approval");
  });
});

const slice: SpecSlice = {
  project_id: "p1",
  requirements: [{ ...spec.requirements[0]!, served_by: ["B1"] } as SpecSlice["requirements"][0]],
  decisions: [],
  rules: [
    { id: "ru1", text: "No new deps", source: "D1", applies_to: ["*"], strength: "binding", status: "active", verdicts: { met: 1, unmet: 0 } },
    { id: "ru2", text: "Tests for endpoints", source: "you", applies_to: ["*"], strength: "binding", status: "active", verdicts: { met: 0, unmet: 2 } },
  ],
  checks: { "R4.3": { state: "met", run_id: "r2" } },
};

describe("review and co-sign", () => {
  function setup(extra: Partial<RunBranchWire> = {}) {
    window.localStorage.setItem(detailKey("p1"), "technical");
    handlers["GET /api/projects/p1/backlog"] = () => ({ json: { items: [] } });
    handlers["GET /api/projects/p1/spec"] = () => ({ json: spec });
    handlers["GET /api/projects/p1/plan"] = () => ({ json: { messages: [], draft: null } });
    handlers["GET /api/runs/r1/spec-slice"] = () => ({ json: slice });
    let current = run("r1", { needs_cosign: true, ...extra });
    handlers["GET /api/projects/p1/runs-review"] = () => ({ json: { main: "a", runs: [current] } });
    handlers["POST /api/projects/p1/runs-review/r1/cosign"] = (b) => {
      current = { ...current, cosign: { verdict: (b as { verdict: "cosign" | "changes" }).verdict, by: "reviewer", at: 1, comments: [] } };
      return { json: {} };
    };
    handlers["POST /api/projects/p1/runs-review/r1/accept"] = () => ({ json: { run: current, items: [] } });
    handlers["POST /api/projects/p1/plan/after-session/r1"] = () => ({ json: { messages: [], draft: null } });
    wrap(<ProjectWork projectId="p1" onStartRun={vi.fn()} seats={{ lead: false, reviewer: "all" }} />);
  }

  it("shows the run against the spec, with the slice's latest check states and rule verdicts", async () => {
    setup();
    const panel = within(await screen.findByTestId("spec-results-r1"));
    expect(panel.getByText("(2/3 checks)")).toBeInTheDocument();
    expect(panel.getByLabelText("rule met")).toBeInTheDocument();
    expect(panel.getByLabelText("rule broken")).toBeInTheDocument();
    expect(panel.getByText("0 met, 2 unmet")).toBeInTheDocument();
  });

  it("keeps Accept disabled until co-signed, then accepts and asks the PM to review the session", async () => {
    setup();
    const accept = await screen.findByRole("button", { name: "Accept into the project" });
    expect(accept).toBeDisabled();
    expect(accept.parentElement).toHaveAttribute("title", expect.stringMatching(/Co-sign the code first/));

    fireEvent.change(screen.getByLabelText("Comment file"), { target: { value: "app.py" } });
    fireEvent.change(screen.getByLabelText("Comment line"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("Comment severity"), { target: { value: "should_fix" } });
    fireEvent.change(screen.getByLabelText("Comment text"), { target: { value: "Guard the empty case" } });
    fireEvent.click(screen.getByRole("button", { name: "Add comment" }));
    expect(screen.getByText(/Guard the empty case/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Co-sign" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Accept into the project" })).toBeEnabled());
    const cosign = calls.find((c) => c.path.endsWith("/cosign"))!;
    expect(cosign.body).toEqual({
      verdict: "cosign",
      comments: [{ text: "Guard the empty case", severity: "should_fix", file: "app.py", line: 12 }],
    });

    fireEvent.click(screen.getByRole("button", { name: "Accept into the project" }));
    expect(await screen.findByText(/Jack is reviewing the session for spec changes/)).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.path === "/api/projects/p1/plan/after-session/r1")).toBe(true));
    expect(screen.getByRole("link", { name: "See Plan." })).toHaveAttribute("href", "/projects/p1?tab=plan");
  });

  it("co-signs with a follow-up item holding the comments, and can request changes", async () => {
    setup();
    handlers["POST /api/projects/p1/backlog"] = (b) => ({ json: item("B9", { title: (b as { title: string }).title }) });
    await screen.findByTestId("cosign-r1");
    fireEvent.change(screen.getByLabelText("Comment text"), { target: { value: "Rename the helper" } });
    fireEvent.click(screen.getByRole("button", { name: "Add comment" }));
    fireEvent.click(screen.getByRole("button", { name: "Co-sign, fix in a follow-up" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/api/projects/p1/backlog")).toBe(true));
    expect(calls.find((c) => c.path === "/api/projects/p1/backlog" && c.method === "POST")!.body).toMatchObject({
      acceptance: ["Rename the helper"],
    });
  });

  it("sends the changes verdict for Request changes", async () => {
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Request changes" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/cosign"))?.body).toMatchObject({ verdict: "changes" }));
    expect(await screen.findByText("changes requested")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept into the project" })).toBeDisabled();
  });

  it("needs no co-sign when the server does not ask for one", async () => {
    setup({ needs_cosign: false });
    expect(await screen.findByRole("button", { name: "Accept into the project" })).toBeEnabled();
    expect(screen.queryByTestId("cosign-r1")).toBeNull();
  });
});

describe("FollowPanel", () => {
  it("lists requirements with met/total, decisions and rules with flags", async () => {
    handlers["GET /api/runs/r1/spec-slice"] = () => ({
      json: { ...slice, decisions: [{ id: "D1", title: "Storage", choice: "SQLite", reason: "", serves: [], binding: true, status: "accepted", decided_by: "pm", supersedes: null, version: 1 }] },
    });
    wrap(<FollowPanel runId="r1" />);
    const panel = within(await screen.findByTestId("follow-panel"));
    expect(panel.getByText("2/3")).toBeInTheDocument();
    expect(panel.getByText(/Storage: SQLite/)).toBeInTheDocument();
    expect(panel.getByLabelText("rule met")).toBeInTheDocument();
    expect(panel.getByLabelText("rule flagged")).toBeInTheDocument();
    expect(panel.getByText("Tests for endpoints (2 unmet)")).toBeInTheDocument();
    expect(panel.getByText(/A flagged rule goes back to the builder as a review issue/)).toBeInTheDocument();
  });

  it("hides itself when the slice has no requirements and no rules", async () => {
    handlers["GET /api/runs/r1/spec-slice"] = () => ({ json: { ...slice, requirements: [], rules: [] } });
    wrap(<FollowPanel runId="r1" />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("follow-panel")).toBeNull();
  });
});
