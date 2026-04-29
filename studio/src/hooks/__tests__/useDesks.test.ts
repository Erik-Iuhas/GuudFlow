import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { useDesks } from "../useDesks";
import { RunWsProvider } from "../../contexts/RunWsContext";
import type { RunWsSubscriber } from "../../contexts/RunWsContext";
import type { Signal } from "../../types";

const mockDesks = vi.fn();

vi.mock("../../api", () => ({
  runsApi: { desks: () => mockDesks() },
}));

const EMPTY_RESPONSE = { desks: {}, tasks_by_id: {} };
const DESK_RESPONSE = {
  desks: {
    developer: [
      {
        task_id: "t1", story_id: "s1", parent_task_id: null,
        assigned_to: "developer", created_by: "architect",
        created_at_iter: 1, goal: "Build login",
        acceptance_criteria: [], out_of_scope: [], references: [],
        status: "IN_PROGRESS", started_at_iter: 1,
        completed_at_iter: null, closed_by_commit: null,
      },
    ],
  },
  tasks_by_id: {},
};

describe("useDesks", () => {
  beforeEach(() => {
    mockDesks.mockResolvedValue(EMPTY_RESPONSE);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("fetches desks on mount", async () => {
    const { result } = renderHook(() => useDesks("run-1"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockDesks).toHaveBeenCalledTimes(1);
    expect(result.current.data).toEqual({ desks: {}, tasksById: {} });
  });

  it("refetches on TASK_COMPLETED signal from RunWsProvider", async () => {
    mockDesks.mockResolvedValue(EMPTY_RESPONSE);
    let emitSignal: (s: Signal) => void = () => {};
    const subscriber: RunWsSubscriber = {
      subscribe: (handler) => {
        emitSignal = handler;
        return () => {};
      },
    };
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      createElement(RunWsProvider, { value: subscriber }, children);

    const { result } = renderHook(() => useDesks("run-1"), { wrapper });
    await waitFor(() => expect(mockDesks).toHaveBeenCalledTimes(1));

    mockDesks.mockResolvedValue(DESK_RESPONSE);
    act(() => {
      emitSignal({ id: "s1", type: "TASK_COMPLETED", summary: "", from_user: false, priority: "normal", iteration: 1, seen_by: [], timestamp: "" } as Signal);
    });

    // Debounce fires after 500ms
    await act(async () => { await new Promise((r) => setTimeout(r, 600)); });
    await waitFor(() => expect(mockDesks).toHaveBeenCalledTimes(2));
    expect(result.current.data?.desks.developer).toHaveLength(1);
  });

  it("debounce coalesces two signals within 500ms into one refetch", async () => {
    let emitSignal: (s: Signal) => void = () => {};
    const subscriber: RunWsSubscriber = {
      subscribe: (handler) => { emitSignal = handler; return () => {}; },
    };
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      createElement(RunWsProvider, { value: subscriber }, children);

    const { result: _ } = renderHook(() => useDesks("run-1"), { wrapper });
    await waitFor(() => expect(mockDesks).toHaveBeenCalledTimes(1));

    const sig = { id: "s1", type: "TASK_STARTED", summary: "", from_user: false, priority: "normal", iteration: 1, seen_by: [], timestamp: "" } as Signal;
    act(() => { emitSignal(sig); });
    act(() => { emitSignal(sig); });

    await act(async () => { await new Promise((r) => setTimeout(r, 600)); });
    // Should be only 2 total: initial + 1 debounced (not 3)
    expect(mockDesks).toHaveBeenCalledTimes(2);
  });

  it("falls back to 5s polling outside RunWsProvider", async () => {
    vi.useFakeTimers();
    const { result: _ } = renderHook(() => useDesks("run-1"));
    await act(async () => { await Promise.resolve(); });
    expect(mockDesks).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(5001); });
    expect(mockDesks).toHaveBeenCalledTimes(2);
  });
});
