import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ApprovalCard } from "../../ApprovalCard";
import type { Signal } from "../../../types";

function approval(detail: string): Signal {
  return {
    id: "a1", type: "APPROVAL_NEEDED", summary: "builder wants to run bash", detail,
    from_agent: "runner", from_user: false, priority: "normal", iteration: 1,
    seen_by: [], timestamp: "2026-01-01T00:00:00.000000",
  };
}

const TOOL_DETAIL = "approval_id=appr_1; kind=tool_permission; role=builder; permission=bash; patterns=pytest -q,npm test";

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true, decision: "granted" }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function lastBody(): unknown {
  const [, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return JSON.parse((init as RequestInit).body as string);
}

describe("ApprovalCard tool_permission", () => {
  it("shows context and the three buttons instead of grant/deny", () => {
    render(<ApprovalCard runId="r1" signal={approval(TOOL_DETAIL)} />);
    expect(screen.getByText("Allow once")).toBeInTheDocument();
    expect(screen.getByText("Always (this run)")).toBeInTheDocument();
    expect(screen.getByText("Reject")).toBeInTheDocument();
    expect(screen.queryByText("Grant")).toBeNull();
    expect(screen.queryByText("Deny")).toBeNull();
    const ctx = screen.getByTestId("tool-permission-context");
    expect(ctx).toHaveTextContent("builder");
    expect(ctx).toHaveTextContent("bash");
    expect(ctx).toHaveTextContent("pytest -q, npm test");
  });

  it("posts each of the three bodies to the approval endpoint", async () => {
    const onDecided = vi.fn();
    const { unmount } = render(<ApprovalCard runId="r1" signal={approval(TOOL_DETAIL)} onDecided={onDecided} />);

    fireEvent.click(screen.getByText("Allow once"));
    await waitFor(() => expect(onDecided).toHaveBeenCalledWith("granted"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/r1/approval/appr_1");
    expect(lastBody()).toEqual({ decision: "grant", scope: "once" });
    unmount();

    render(<ApprovalCard runId="r1" signal={approval(TOOL_DETAIL)} onDecided={onDecided} />);
    fireEvent.click(screen.getByText("Always (this run)"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(lastBody()).toEqual({ decision: "grant", scope: "run" });

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, decision: "denied" }), { status: 200 }));
    fireEvent.click(await screen.findByText("Reject"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(lastBody()).toEqual({ decision: "deny" });
  });

  it("keeps the Grant / Deny buttons for other approval kinds", () => {
    render(<ApprovalCard runId="r1" signal={approval("approval_id=appr_2; kind=heavy_tier_dispatch; estimated_cost_usd=0.42")} />);
    expect(screen.getByText("Grant")).toBeInTheDocument();
    expect(screen.getByText("Deny")).toBeInTheDocument();
    expect(screen.queryByText("Allow once")).toBeNull();
  });
});
