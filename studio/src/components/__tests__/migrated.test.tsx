import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AgentStateCard } from "../AgentStateCard";
import { ApprovalCard } from "../ApprovalCard";
import { CostStrip } from "../CostStrip";
import { ToastProvider, useToast } from "../Toaster";
import type { AgentLiveSummary } from "../../lib/iterations";
import type { ProjectConfig, Signal } from "../../types";

const summary = (over: Partial<AgentLiveSummary> = {}): AgentLiveSummary => ({
  role: "builder", state: "running", iteration: 2, lastCommit: null, lastCommitMessage: null,
  claimedFiles: [], unreadComments: 0, ...over,
});

function signal(id: string, type: string, summaryText: string, from = "builder", iteration = 2): Signal {
  return {
    id, type, summary: summaryText, detail: null, from_agent: from, from_user: false,
    priority: "normal", iteration, seen_by: [], timestamp: "2026-01-01T00:00:00",
  };
}

describe("AgentStateCard", () => {
  it("shows role, state, iteration and exposes the state for styling", () => {
    const { container } = render(<AgentStateCard summary={summary()} allSignals={[]} />);
    expect(screen.getByText("builder")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("iter 2")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveAttribute("data-state", "running");
  });

  it("expands on header click and lists the iteration's signals", () => {
    render(
      <AgentStateCard
        summary={summary()}
        allSignals={[signal("1", "TOOL_CALL", "ran ls"), signal("2", "TOOL_CALL", "other iter", "builder", 9)]}
      >
        <div>child content</div>
      </AgentStateCard>,
    );
    expect(screen.queryByText("child content")).toBeNull();
    fireEvent.click(screen.getByText("builder"));
    expect(screen.getByText("child content")).toBeInTheDocument();
    expect(screen.getByText("1 signal")).toBeInTheDocument();
    expect(screen.getByText(/ran ls/)).toBeInTheDocument();
    expect(screen.queryByText(/other iter/)).toBeNull();
  });

  it("calls onClick for the card but not when the header toggles", () => {
    const onClick = vi.fn();
    render(<AgentStateCard summary={summary()} allSignals={[]} onClick={onClick} />);
    fireEvent.click(screen.getByText("builder"));
    expect(onClick).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("running"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("offers Retry only for failed agents and runs the handler without bubbling", async () => {
    const onRetry = vi.fn().mockResolvedValue(undefined);
    const onClick = vi.fn();
    const { rerender } = render(
      <AgentStateCard summary={summary()} allSignals={[]} onRetry={onRetry} onClick={onClick} />,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    rerender(<AgentStateCard summary={summary({ state: "failed" })} allSignals={[]} onRetry={onRetry} onClick={onClick} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(onRetry).toHaveBeenCalledTimes(1));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("shows persona name and unread comment count", () => {
    render(
      <AgentStateCard summary={summary({ unreadComments: 3 })} allSignals={[]} personaName="Ada" />,
    );
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("3 unread comments")).toBeInTheDocument();
  });
});

describe("CostStrip", () => {
  const project: ProjectConfig = {
    project_id: "p", goal: "g", max_iterations: 3,
    agents: [
      { role: "dev", model_tier: "solid" },
      { role: "lead", model_tier: "heavy" },
    ] as ProjectConfig["agents"],
  };

  it("renders the total and one entry per agent with its tier", () => {
    render(<CostStrip project={project} />);
    expect(screen.getByTestId("cost-strip")).toHaveTextContent("per run (estimate)");
    expect(screen.getByText(/^dev \(solid, 3×\)/)).toBeInTheDocument();
    expect(screen.getByText(/^lead \(heavy, 3×\)/)).toBeInTheDocument();
  });

  it("colours tiers with status tokens, not hex", () => {
    render(<CostStrip project={project} />);
    expect(screen.getByText(/^dev \(solid/).className).toContain("text-status-running");
    expect(screen.getByText(/^lead \(heavy/).className).toContain("text-status-waiting");
  });
});

describe("Toaster", () => {
  function Pusher() {
    const { push } = useToast();
    return (
      <>
        <button onClick={() => push("saved", "success")}>ok</button>
        <button onClick={() => push("boom", "error")}>bad</button>
      </>
    );
  }

  it("pushes toasts with their kind and dismisses on close", async () => {
    render(
      <ToastProvider>
        <Pusher />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("ok"));
    fireEvent.click(screen.getByText("bad"));
    expect(await screen.findByText("saved")).toBeInTheDocument();
    expect(screen.getByText("boom").closest("[data-variant]")).toHaveAttribute("data-variant", "error");
    const closers = screen.getAllByRole("button", { name: "dismiss" });
    expect(closers).toHaveLength(2);
    fireEvent.click(closers[0]);
    await waitFor(() => expect(screen.queryByText("saved")).toBeNull());
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("useToast throws outside the provider", () => {
    const swallow = (e: ErrorEvent) => e.preventDefault();
    window.addEventListener("error", swallow);
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Pusher />)).toThrow(/ToastProvider/);
    spy.mockRestore();
    window.removeEventListener("error", swallow);
  });

  it("auto-dismisses after the timeout", async () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <Pusher />
        </ToastProvider>,
      );
      fireEvent.click(screen.getByText("ok"));
      expect(screen.getByText("saved")).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(6000);
      });
      expect(screen.queryByText("saved")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("ApprovalCard (grant / deny)", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ ok: true, approval_id: "a1", decision: "granted" }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const gate: Signal = {
    id: "g1", type: "APPROVAL_NEEDED", summary: "Run the heavy model?",
    detail: "approval_id=a1; kind=heavy_tier_dispatch; estimated_cost_usd=0.42",
    from_agent: "runner", from_user: false, priority: "normal", iteration: 1, seen_by: [], timestamp: "t",
  };

  it("shows the summary, kind, id and estimated cost", () => {
    render(<ApprovalCard runId="r1" signal={gate} />);
    expect(screen.getByText("Run the heavy model?")).toBeInTheDocument();
    expect(screen.getByText("heavy_tier_dispatch")).toBeInTheDocument();
    expect(screen.getByText("a1")).toBeInTheDocument();
    expect(screen.getByText("$0.42")).toBeInTheDocument();
  });

  it("posts the grant with the optional note and reports the decision", async () => {
    const onDecided = vi.fn();
    render(<ApprovalCard runId="r1" signal={gate} onDecided={onDecided} />);
    fireEvent.change(screen.getByPlaceholderText(/Optional note/), { target: { value: " go ahead " } });
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("granted"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/runs/r1/approval/a1");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ decision: "grant", note: "go ahead" });
  });

  it("posts a deny", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true, approval_id: "a1", decision: "denied" }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    const onDecided = vi.fn();
    render(<ApprovalCard runId="r1" signal={gate} onDecided={onDecided} />);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("denied"));
  });

  it("shows the error and re-enables the buttons when the request fails", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "approval already decided" }), { status: 404, headers: { "content-type": "application/json" } }));
    render(<ApprovalCard runId="r1" signal={gate} />);
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("approval already decided");
    expect(screen.getByRole("button", { name: "Grant" })).not.toBeDisabled();
  });

  it("renders nothing when the detail is malformed", () => {
    const { container } = render(<ApprovalCard runId="r1" signal={{ ...gate, detail: "garbage" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
