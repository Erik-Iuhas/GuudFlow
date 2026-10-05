import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";

vi.mock("../../../api", async (orig) => {
  const actual = await orig<typeof import("../../../api")>();
  return {
    ...actual,
    connectQuipStream: vi.fn(() => ({ close: vi.fn(), isConnected: () => true })),
    connectRunStream: vi.fn(() => ({ close: vi.fn(), isConnected: () => true })),
    getCards: vi.fn(async () => []),
  };
});

import { createRunStore, type RunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import { TooltipProvider } from "../../../components/ui/tooltip";
import type { RawSignal, SignalType } from "../../../lib/runEvents.gen";
import { mockElementSize, mockViewport } from "../../../test-utils/dom";
import { AgentDetail, AgentLane } from "../AgentLane";
import { ApprovalsBar } from "../ApprovalsBar";
import { BudgetMeter } from "../BudgetMeter";
import { CockpitView } from "../Cockpit";
import { OverseerPanel } from "../OverseerPanel";
import { PipelineGraph } from "../PipelineGraph";
import { roleSpecs } from "../model";
import fixture from "../__fixtures__/run2000.json";

let n = 0;
function sig(type: SignalType, from: string | null, detail: unknown = null, summary = type.toLowerCase()): RawSignal {
  n += 1;
  return {
    id: `t${n}`, type, summary, from_agent: from, priority: "normal", iteration: 1,
    detail: detail === null ? null : typeof detail === "string" ? detail : JSON.stringify(detail),
    timestamp: `2026-03-01T10:00:${String(n % 60).padStart(2, "0")}+00:00`,
  };
}

function wrap(store: RunStore, ui: ReactNode, qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TooltipProvider>
          <RunStoreProvider store={store}>{ui}</RunStoreProvider>
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const project = {
  project_id: "p1", goal: "g", approval_gates: { token_budget_usd: 5 },
  agents: [
    { role: "planner" },
    { role: "dev", depends_on: ["planner"] },
    { role: "reviewer", depends_on: ["dev"], review_of: ["dev"], max_review_rounds: 3 },
    { role: "overseer" },
  ],
};

beforeEach(() => {
  mockElementSize();
  mockViewport(true);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PipelineGraph", () => {
  it("renders a node per role with a text state label, the review round, and the overseer pulsing", async () => {
    const store = createRunStore("r1");
    store.getState().ingest([
      sig("STARTED", "planner"),
      sig("COMPLETE", "planner"),
      sig("STARTED", "dev"),
      sig("STARTED", "overseer"),
      sig("REJECTED", "reviewer", { task_id: "T1", authors: ["dev"], round: 2 }),
      sig("BLOCKED", "reviewer", { task_id: "T1", on: "dev", need: "a fix" }),
    ]);
    const { container } = wrap(store, <PipelineGraph specs={roleSpecs(project.agents)} />);

    const node = (r: string) => screen.getByTestId(`graph-node-${r}`);
    expect(node("planner")).toHaveAttribute("data-state", "complete");
    expect(within(node("planner")).getByText(/complete/)).toBeInTheDocument();
    expect(node("dev")).toHaveAttribute("data-state", "running");
    expect(node("reviewer")).toHaveAttribute("data-state", "blocked");
    expect(node("overseer").className).toMatch(/animate-pulse/);
    expect(node("dev").className).not.toMatch(/animate-pulse/);

    await waitFor(() => expect(container.querySelectorAll(".react-flow__edge").length).toBeGreaterThanOrEqual(4));
    expect(container.querySelector(".cockpit-edge-review")).not.toBeNull();
    expect(container.querySelector(".cockpit-edge-blocked")).not.toBeNull();
    expect(container.querySelectorAll(".cockpit-edge-depends")).toHaveLength(2);
    expect(screen.getByText("round 2/3")).toBeInTheDocument();
  });

  it("selecting a node reports the role", () => {
    const onSelect = vi.fn();
    wrap(createRunStore("r1"), <PipelineGraph specs={roleSpecs(project.agents)} onSelectRole={onSelect} />);
    fireEvent.click(screen.getByTestId("graph-node-dev"));
    expect(onSelect).toHaveBeenCalledWith("dev");
  });
});

describe("AgentLane", () => {
  it("shows the card chip, a short last result and the capability badge; details hold the tool calls", () => {
    const store = createRunStore("r1");
    store.getState().ingest([
      sig("CAPABILITIES", "dev", {
        role_type: "developer", requested: {}, effective: { sandbox: "strict", web: "off", shell: "on" },
        digest: "d", mcp_tools: [], model: "m", warnings: [],
      }),
      sig("STARTED", "dev"),
      sig("TASK_ASSIGNED", "planner", "T1", "task:T1 → dev: Build the form"),
      sig("TASK_MOVED", "runner", { task_id: "T1", from: "todo", to: "in_progress", reason: "x", owner: "dev", round: 1 }),
      sig("TOOL_CALL", "dev", { tool: "edit", status: "completed", input: { filePath: "src/form.ts" }, output: "ok", duration_ms: 12 }),
      sig("AGENT_RESULT", "dev", { result: { status: "done", summary: "Form is built" }, cost: 0.25 }, "dev result"),
    ]);
    const onOpen = vi.fn();
    wrap(store, <><AgentLane role="dev" onOpen={onOpen} /><AgentDetail role="dev" /></>);

    const lane = screen.getByRole("region", { name: "dev" });
    expect(lane).toBeInTheDocument();
    const chip = screen.getByTestId("lane-card-dev");
    expect(within(chip).getByText("T1")).toBeInTheDocument();
    expect(within(chip).getByText("Build the form")).toBeInTheDocument();
    expect(within(chip).getByText("round 1")).toBeInTheDocument();
    // Tool calls live in the details, not on the compact card.
    expect(within(lane).queryByTestId("toolfeed-dev")).toBeNull();
    expect(within(screen.getByTestId("toolfeed-dev")).getByText("src/form.ts")).toBeInTheDocument();
    expect(within(screen.getByTestId("agent-detail-dev")).getByText("Form is built")).toBeInTheDocument();
    fireEvent.click(within(lane).getByRole("button", { name: "Details for dev" }));
    expect(onOpen).toHaveBeenCalledWith("dev");
    expect(within(screen.getByTestId("lane-result-dev")).getByText("Form is built")).toBeInTheDocument();
    expect(screen.getByTestId("lane-result-dev")).toHaveTextContent("$0.25");

    const badge = screen.getByTestId("capability-badge-dev");
    expect(badge).toHaveTextContent("developer · 2/3 on");
    expect(badge.getAttribute("aria-label")).toContain("sandbox: strict, web: off, shell: on");
    fireEvent.focus(badge);
    return waitFor(() => expect(screen.getAllByText("web: off").length).toBeGreaterThan(0));
  });

  it("shows empty states for an idle role", () => {
    wrap(createRunStore("r1"), <><AgentLane role="dev" /><AgentDetail role="dev" /></>);
    expect(screen.getByText("No active card")).toBeInTheDocument();
    expect(screen.getByText("No tool calls yet.")).toBeInTheDocument();
    expect(screen.getAllByText("No result yet.")).toHaveLength(2);
  });
});

describe("ApprovalsBar", () => {
  const toolDetail = (id: string) =>
    `approval_id=${id}; kind=tool_permission; context=${JSON.stringify({ role: "dev", permission: "bash", patterns: ["pip install x"] })}`;
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response(JSON.stringify({ decision: "granted" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
  });
  const lastCall = () => {
    const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1] as [string, RequestInit];
    return { url, body: JSON.parse(String(init.body)) as Record<string, unknown> };
  };

  it("shows an empty state with nothing pending", () => {
    wrap(createRunStore("r1"), <ApprovalsBar runId="r1" />);
    expect(screen.getByText("No approvals pending.")).toBeInTheDocument();
  });

  it("tool_permission: once, always (scope=run) and reject post the right bodies", async () => {
    const store = createRunStore("r1");
    store.getState().ingest([
      sig("APPROVAL_NEEDED", "dev", toolDetail("a1")),
      sig("APPROVAL_NEEDED", "dev", toolDetail("a2")),
      sig("APPROVAL_NEEDED", "dev", toolDetail("a3")),
    ]);
    wrap(store, <ApprovalsBar runId="r1" />);
    const items = screen.getAllByTestId("approval-item");
    expect(items).toHaveLength(3);
    expect(within(items[0]).getByTestId("tool-permission-context")).toHaveTextContent("pip install x");

    fireEvent.click(within(items[0]).getByRole("button", { name: "Allow once" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(lastCall().url).toBe("/api/runs/r1/approval/a1");
    expect(lastCall().body).toEqual({ decision: "grant", scope: "once" });

    fireEvent.click(within(items[1]).getByRole("button", { name: /Always/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(lastCall().url).toBe("/api/runs/r1/approval/a2");
    expect(lastCall().body).toEqual({ decision: "grant", scope: "run" });

    fireEvent.click(within(items[2]).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(lastCall().body).toEqual({ decision: "deny" });
  });

  it("other gates: grant and deny", async () => {
    const store = createRunStore("r1");
    store.getState().ingest([
      sig("APPROVAL_NEEDED", "runner", "approval_id=b1; kind=pre_run_cost_preview; estimated_cost_usd=1.5"),
      sig("APPROVAL_NEEDED", "runner", "approval_id=b2; kind=heavy_tier_dispatch"),
    ]);
    wrap(store, <ApprovalsBar runId="r1" />);
    const items = screen.getAllByTestId("approval-item");
    expect(items[0]).toHaveTextContent("~$1.50");
    expect(within(items[0]).queryByRole("button", { name: "Allow once" })).toBeNull();

    fireEvent.click(within(items[0]).getByRole("button", { name: "Grant" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(lastCall().url).toBe("/api/runs/r1/approval/b1");
    expect(lastCall().body).toMatchObject({ decision: "grant" });

    fireEvent.click(within(items[1]).getByRole("button", { name: "Deny" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(lastCall().body).toMatchObject({ decision: "deny" });
  });

  it("shows the error when the decision fails and keeps the buttons", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "approval expired" }), { status: 410 }));
    const store = createRunStore("r1");
    store.getState().ingest([sig("APPROVAL_NEEDED", "dev", toolDetail("a1"))]);
    wrap(store, <ApprovalsBar runId="r1" />);
    fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approval expired");
    expect(screen.getByRole("button", { name: "Allow once" })).toBeEnabled();
  });

  it("drops an approval when its GRANTED signal arrives", () => {
    const store = createRunStore("r1");
    store.getState().ingest([sig("APPROVAL_NEEDED", "dev", toolDetail("a1"))]);
    wrap(store, <ApprovalsBar runId="r1" />);
    expect(screen.getAllByTestId("approval-item")).toHaveLength(1);
    act(() => store.getState().ingest(sig("APPROVAL_GRANTED", "runner", "approval_id=a1; kind=tool_permission")));
    expect(screen.queryAllByTestId("approval-item")).toHaveLength(0);
  });
});

describe("BudgetMeter", () => {
  function costStore(costs: Record<string, number>) {
    const store = createRunStore("r1");
    store.getState().ingest(Object.entries(costs).map(([role, cost]) => sig("AGENT_RESULT", role, { result: {}, cost })));
    return store;
  }

  it("shows spent against the cap and a progress bar", () => {
    wrap(costStore({ dev: 1, reviewer: 1.5 }), <BudgetMeter budgetUsd={5} />);
    const meter = screen.getByTestId("budget-meter");
    expect(meter).toHaveTextContent("$2.50");
    expect(meter).toHaveTextContent("/ $5.00");
    expect(meter).toHaveTextContent("50%");
    expect(meter).toHaveAttribute("data-level", "ok");
    const bar = within(meter).getByRole("progressbar", { name: "Budget spent" });
    expect(bar).toHaveAttribute("aria-valuemax", "5");
    expect(bar).toHaveAttribute("aria-valuenow", "2.5");
  });

  it("flags nearing the cap and over budget with text", () => {
    const { unmount } = wrap(costStore({ dev: 4.2 }), <BudgetMeter budgetUsd={5} />);
    expect(screen.getByTestId("budget-meter")).toHaveTextContent("nearing the cap");
    unmount();
    wrap(costStore({ dev: 6 }), <BudgetMeter budgetUsd={5} />);
    expect(screen.getByTestId("budget-meter")).toHaveTextContent("over budget");
    expect(screen.getByTestId("budget-meter").querySelector("[role=progressbar]")).toHaveAttribute("aria-valuenow", "5");
  });

  it("lists the per-agent breakdown on focus, largest first", async () => {
    wrap(costStore({ dev: 1, reviewer: 3 }), <BudgetMeter budgetUsd={8} />);
    fireEvent.focus(screen.getByTestId("budget-meter"));
    const table = await screen.findAllByTestId("budget-breakdown");
    const rows = within(table[0]).getAllByRole("row").map((r) => r.textContent);
    expect(rows[0]).toContain("reviewer");
    expect(rows[0]).toContain("$3.00");
    expect(rows[0]).toContain("75%");
    expect(rows[1]).toContain("dev");
  });

  it("without a cap shows spend only", () => {
    wrap(costStore({ dev: 1 }), <BudgetMeter />);
    const meter = screen.getByTestId("budget-meter");
    expect(meter).toHaveTextContent("no cap set");
    expect(meter.querySelector("[role=progressbar]")).toBeNull();
  });
});

describe("OverseerPanel", () => {
  it("lists interventions with type, target, rationale and the needs-human badge", () => {
    const store = createRunStore("r1");
    store.getState().ingest([
      sig("OVERSEER_INTERVENTION", "overseer", { type: "rebrief", rationale: "Same issue twice", needs_human: false, role: "dev", task_id: "T2" }),
      sig("OVERSEER_INTERVENTION", "overseer", { type: "propose_team_change", rationale: "Add a tester", needs_human: true }),
    ]);
    wrap(store, <OverseerPanel />);
    const items = screen.getAllByTestId("intervention");
    expect(items[1]).toHaveTextContent("propose team change");
    expect(items[1]).toHaveTextContent("needs human");
    expect(items[0]).toHaveTextContent("rebrief");
    expect(items[0]).toHaveTextContent("dev · card T2");
    expect(items[0]).toHaveTextContent("Same issue twice");
    expect(items[0]).not.toHaveTextContent("needs human");
  });

  it("has an empty state", () => {
    wrap(createRunStore("r1"), <OverseerPanel />);
    expect(screen.getByText(/No wakes/)).toBeInTheDocument();
  });
});

const socketOk = { status: "open" as const, error: null, retry: () => {} };
const view = (store: RunStore, extra: Partial<React.ComponentProps<typeof CockpitView>> = {}) => (
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter>
      <CockpitView runId="r1" store={store} socket={socketOk} project={project} budgetUsd={5} {...extra} />
    </MemoryRouter>
  </QueryClientProvider>
);

describe("CockpitView", () => {
  it("always renders the comment box, in every layout and run state", () => {
    const store = createRunStore("r1");
    const { unmount } = render(view(store));
    expect(screen.getByLabelText("Comment for the agents")).toBeInTheDocument();
    unmount();

    mockViewport(false);
    store.getState().ingest(sig("STARTED", "dev"));
    store.getState().setLive(false);
    render(view(store, { meta: { run_id: "r1", project_id: "p1", status: "complete" } }));
    expect(screen.getByLabelText("Comment for the agents")).toBeInTheDocument();
  });

  it("shows the waiting state before any agent starts, then the lanes", () => {
    const store = createRunStore("r1");
    render(view(store));
    expect(screen.getByTestId("waiting")).toHaveTextContent("Waiting for the first agent…");
    act(() => store.getState().ingest(sig("STARTED", "dev")));
    expect(screen.queryByTestId("waiting")).toBeNull();
    expect(screen.getByRole("region", { name: "dev" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "overseer" })).toBeInTheDocument();
  });

  it("clicking an agent's details opens its tool calls in a sheet", async () => {
    const store = createRunStore("r1");
    store.getState().ingest([
      sig("STARTED", "dev"),
      sig("TOOL_CALL", "dev", { tool: "edit", status: "completed", input: { filePath: "src/app.ts" }, output: "ok", duration_ms: 3 }),
    ]);
    render(view(store));
    expect(screen.queryByTestId("toolfeed-dev")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Details for dev" }));
    const detail = await screen.findByTestId("agent-detail-dev");
    expect(within(detail).getByTestId("toolfeed-dev")).toBeInTheDocument();
  });

  it("a finished run's elapsed time runs from its first signal, not its saved meta", () => {
    const store = createRunStore("r1");
    store.getState().ingest([
      { ...sig("STARTED", "dev"), timestamp: "2026-03-01T10:00:00+00:00" },
      { ...sig("COMPLETE", "dev"), timestamp: "2026-03-01T10:12:30+00:00" },
    ]);
    store.getState().setLive(false);
    // meta.created_at is written when the run is saved, i.e. at the end.
    render(view(store, { meta: { run_id: "r1", project_id: "p1", status: "complete", created_at: "2026-03-01T10:12:31+00:00" } }));
    expect(screen.getByTestId("elapsed")).toHaveTextContent("12:30");
  });

  it("shows a socket error with a retry", () => {
    const retry = vi.fn();
    render(view(createRunStore("r1"), { socket: { status: "error", error: "run not found or finished", retry } }));
    expect(screen.getByTestId("socket-error")).toHaveTextContent("run not found or finished");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
  });

  it("keyboard: c focuses the comment box, a the first approval, g the graph", () => {
    const store = createRunStore("r1");
    store.getState().ingest(sig("APPROVAL_NEEDED", "dev", "approval_id=a1; kind=heavy_tier_dispatch"));
    render(view(store));
    fireEvent.keyDown(window, { key: "c" });
    expect(screen.getByLabelText("Comment for the agents")).toHaveFocus();
    // Typing into the box must not trigger shortcuts.
    fireEvent.keyDown(screen.getByLabelText("Comment for the agents"), { key: "a" });
    expect(screen.getByLabelText("Comment for the agents")).toHaveFocus();
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(window, { key: "a" });
    expect(screen.getByRole("button", { name: "Grant" })).toHaveFocus();
    fireEvent.keyDown(window, { key: "g" });
    expect(screen.getByLabelText("Pipeline graph", { selector: "div[tabindex]" })).toHaveFocus();
  });

  it("HUMAN_NEEDED shows a banner and focuses the comment box", () => {
    const store = createRunStore("r1");
    render(view(store));
    act(() => store.getState().ingest(sig("HUMAN_NEEDED", "dev", null, "Which database?")));
    expect(screen.getByTestId("human-needed")).toHaveTextContent("Which database?");
    expect(screen.getByLabelText("Comment for the agents")).toHaveFocus();
  });

  it("narrow layout puts the panels in a drawer", async () => {
    mockViewport(false);
    render(view(createRunStore("r1")));
    expect(screen.queryByLabelText("Run panels")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Board and feeds" }));
    expect(await screen.findByRole("tab", { name: "Board" })).toBeInTheDocument();
  });
});

describe("recorded run (2,000 signals)", () => {
  it("has the advertised shape", () => {
    expect(fixture.signals).toHaveLength(2000);
    const types = new Set(fixture.signals.map((s) => s.type));
    for (const t of [
      "STARTED", "CAPABILITIES", "TOOL_CALL", "AGENT_RESULT", "TASK_MOVED", "TASK_THREAD", "REJECTED",
      "OVERSEER_INTERVENTION", "APPROVAL_NEEDED", "APPROVAL_GRANTED", "COMPLETE",
    ]) expect(types.has(t)).toBe(true);
  });

  it("ingests and renders the cockpit within the time budget", async () => {
    const store = createRunStore("r1");
    const t0 = performance.now();
    store.getState().ingest(fixture.signals as unknown as RawSignal[]);
    const ingestMs = performance.now() - t0;
    store.getState().seedCards(fixture.cards as never);
    store.getState().setLive(false);

    const t1 = performance.now();
    render(view(store, { project: fixture.project as never, meta: fixture.meta as never, budgetUsd: 5 }));
    const renderMs = performance.now() - t1;
    console.info(`[perf] ingest 2000 signals: ${ingestMs.toFixed(0)} ms; first cockpit render: ${renderMs.toFixed(0)} ms`);

    expect(ingestMs).toBeLessThan(1500);
    expect(renderMs).toBeLessThan(5000);
    expect(store.getState().signals).toHaveLength(2000);
    // Windowed: far fewer rows in the DOM than signals.
    const rows = within(screen.getByTestId("board-feed")).queryAllByText(/./, { selector: "[data-signal]" });
    expect(document.querySelectorAll("[data-signal]").length).toBeLessThan(400);
    expect(rows.length).toBeLessThan(400);
    expect(screen.getByRole("region", { name: "backend" })).toBeInTheDocument();
    expect(screen.getByTestId("budget-meter")).toHaveTextContent("/ $5.00");
    expect(screen.getByLabelText("Comment for the agents")).toBeInTheDocument();
    expect(store.getState().reviews["reviewer>backend"].round).toBeGreaterThan(0);
  });
});
