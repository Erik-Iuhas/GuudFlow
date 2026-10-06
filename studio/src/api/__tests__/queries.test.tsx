import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createQueryClient, useDecideApproval, useDecideToolPermission, useRunCards, useRunMeta } from "../queries";

let fetchMock: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {children}
  </QueryClientProvider>
);

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("queries", () => {
  it("createQueryClient returns a client", () => {
    expect(createQueryClient().getDefaultOptions().queries?.retry).toBe(1);
  });

  it("useRunMeta fetches the run status", async () => {
    fetchMock.mockResolvedValue(json({ run_id: "r1", project_id: "p", status: "running" }));
    const { result } = renderHook(() => useRunMeta("r1"), { wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("running"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/r1");
  });

  it("useRunMeta stays idle without a run id", () => {
    const { result } = renderHook(() => useRunMeta(undefined), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("useRunCards unwraps the cards list", async () => {
    fetchMock.mockResolvedValue(json({ cards: [{ id: "t1", title: "T", owner: "dev", column: "todo" }] }));
    const { result } = renderHook(() => useRunCards("r1"), { wrapper });
    await waitFor(() => expect(result.current.data).toHaveLength(1));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/r1/cards");
  });

  it("useRunCards exposes errors", async () => {
    fetchMock.mockResolvedValue(json({ detail: "run 'r1' not found" }, 404));
    const { result } = renderHook(() => useRunCards("r1"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("useDecideApproval posts the decision", async () => {
    fetchMock.mockResolvedValue(json({ ok: true, approval_id: "a1", decision: "granted", type: "APPROVAL_GRANTED" }));
    const { result } = renderHook(() => useDecideApproval("r1"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ approvalId: "a1", decision: "grant", note: "ok" });
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/runs/r1/approval/a1");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ decision: "grant", note: "ok" });
  });

  it("useDecideToolPermission posts the scoped body", async () => {
    fetchMock.mockResolvedValue(json({ ok: true, decision: "granted" }));
    const { result } = renderHook(() => useDecideToolPermission("r1"), { wrapper });
    let decision: string | undefined;
    await act(async () => {
      decision = await result.current.mutateAsync({ approvalId: "a2", choice: "run" });
    });
    expect(decision).toBe("granted");
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      decision: "grant",
      scope: "run",
    });
  });
});
