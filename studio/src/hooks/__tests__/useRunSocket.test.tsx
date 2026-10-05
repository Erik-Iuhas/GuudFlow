import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, renderHook, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

type Msg = { type: string; signals?: unknown[]; signal?: unknown; live?: boolean };
interface Conn {
  runId: string;
  onMessage: (m: Msg) => void;
  onState?: (c: boolean) => void;
  close: ReturnType<typeof vi.fn>;
}
const conns: Conn[] = [];

vi.mock("../../api", async (orig) => {
  const actual = await orig<typeof import("../../api")>();
  return {
    ...actual,
    getCards: vi.fn(async () => [
      { id: "t1", title: "From REST", owner: "dev", column: "todo", acceptance: ["ok"] },
    ]),
    connectRunStream: vi.fn(
      (runId: string, onMessage: Conn["onMessage"], _onClose?: () => void, onState?: Conn["onState"]) => {
        const conn: Conn = { runId, onMessage, onState, close: vi.fn() };
        conns.push(conn);
        return { close: conn.close, isConnected: () => true };
      },
    ),
  };
});

import { useRunSocket } from "../useRunSocket";
import { useRunLive } from "../useRunLive";
import { createRunStore } from "../../stores/runEvents";
import { RunStoreProvider, useRunStore } from "../../stores/runStoreContext";

const raw = (id: string, type: string, extra: Record<string, unknown> = {}) => ({
  id, type, summary: type, detail: null, from_agent: "dev", priority: "normal", iteration: 1,
  timestamp: "2026-01-01T00:00:00", ...extra,
});

beforeEach(() => {
  conns.length = 0;
});

describe("useRunSocket", () => {
  it("connects to the run, ingests the snapshot, then live signals", () => {
    const store = createRunStore("r1");
    const { result } = renderHook(() => useRunSocket("r1", store));
    expect(conns).toHaveLength(1);
    expect(conns[0].runId).toBe("r1");
    expect(result.current.status).toBe("connecting");

    act(() => conns[0].onMessage({ type: "snapshot", live: false, signals: [raw("a", "STARTED")] }));
    expect(result.current.status).toBe("open");
    expect(store.getState().live).toBe(false);
    expect(store.getState().agents.dev.state).toBe("running");

    act(() => conns[0].onMessage({ type: "signal", signal: raw("b", "COMPLETE") }));
    expect(store.getState().agents.dev.state).toBe("complete");
    expect(store.getState().signals).toHaveLength(2);
  });

  it("a finished run's close ends the feed instead of reconnecting", () => {
    const store = createRunStore("r1");
    const { result } = renderHook(() => useRunSocket("r1", store));
    act(() => conns[0].onMessage({ type: "snapshot", live: false, signals: [raw("a", "STARTED")] }));
    act(() => conns[0].onState?.(false));
    expect(result.current.status).toBe("open");
    expect(conns[0].close).toHaveBeenCalled();
  });

  it("dedupes the snapshot that follows a reconnect", () => {
    const store = createRunStore("r1");
    const { result } = renderHook(() => useRunSocket("r1", store));
    act(() => conns[0].onMessage({ type: "snapshot", signals: [raw("a", "STARTED")] }));
    act(() => conns[0].onState?.(false));
    expect(result.current.status).toBe("reconnecting");
    act(() => conns[0].onState?.(true));
    act(() => conns[0].onMessage({ type: "snapshot", signals: [raw("a", "STARTED"), raw("b", "COMPLETE")] }));
    expect(result.current.status).toBe("open");
    expect(store.getState().signals).toHaveLength(2);
    expect(store.getState().agents.dev.spawns).toBe(1);
  });

  it("surfaces a server error frame and can retry with a fresh connection", () => {
    const store = createRunStore("r1");
    const { result } = renderHook(() => useRunSocket("r1", store));
    act(() => conns[0].onMessage({ type: "error" }));
    expect(result.current.status).toBe("error");
    expect(result.current.error).toMatch(/not found/);
    act(() => result.current.retry());
    expect(conns[0].close).toHaveBeenCalled();
    expect(conns).toHaveLength(2);
    expect(result.current.status).toBe("connecting");
    expect(result.current.error).toBeNull();
  });

  it("closes the socket on unmount and does not connect without a run id", () => {
    const store = createRunStore("r1");
    const { unmount } = renderHook(() => useRunSocket("r1", store));
    unmount();
    expect(conns[0].close).toHaveBeenCalled();
    renderHook(() => useRunSocket(undefined, store));
    expect(conns).toHaveLength(1);
  });

  it("calls onTaskAssigned when a card is announced", () => {
    const store = createRunStore("r1");
    const onTaskAssigned = vi.fn();
    renderHook(() => useRunSocket("r1", store, { onTaskAssigned }));
    act(() =>
      conns[0].onMessage({
        type: "signal",
        signal: raw("a", "TASK_ASSIGNED", { detail: "t9", summary: "task:t9 → dev: x" }),
      }),
    );
    expect(onTaskAssigned).toHaveBeenCalledTimes(1);
  });
});

describe("useRunLive", () => {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  );

  it("seeds the store with the REST cards", async () => {
    const { result } = renderHook(() => useRunLive("r1"), { wrapper });
    await vi.waitFor(() => expect(result.current.store.getState().cards.t1?.title).toBe("From REST"));
    expect(result.current.store.getState().cards.t1.acceptance).toEqual(["ok"]);
  });
});

describe("useRunStore", () => {
  function Probe({ id }: { id: string }) {
    const card = useRunStore((s) => s.cards[id]);
    return <div data-testid="card">{card ? `${card.column}` : "none"}</div>;
  }

  it("re-renders a selector subscriber when its slice changes", () => {
    const store = createRunStore("r1");
    render(
      <RunStoreProvider store={store}>
        <Probe id="t1" />
      </RunStoreProvider>,
    );
    expect(screen.getByTestId("card")).toHaveTextContent("none");
    act(() =>
      store.getState().ingest(
        raw("m", "TASK_MOVED", {
          detail: JSON.stringify({ task_id: "t1", from: "todo", to: "review", reason: "", owner: "dev", round: 1 }),
        }) as never,
      ),
    );
    expect(screen.getByTestId("card")).toHaveTextContent("review");
  });

  it("throws outside a provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    // React re-dispatches render errors as window "error" events; swallow them to keep output clean.
    const swallow = (e: ErrorEvent) => e.preventDefault();
    window.addEventListener("error", swallow);
    expect(() => render(<Probe id="t1" />)).toThrow(/RunStoreProvider/);
    window.removeEventListener("error", swallow);
    spy.mockRestore();
  });
});
