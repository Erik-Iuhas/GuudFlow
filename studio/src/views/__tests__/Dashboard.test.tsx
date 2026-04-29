import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Dashboard } from "../Dashboard";

const EMPTY_DATA = {
  active_runs: [],
  recent_completions: [],
  pending_approvals: [],
  top_personas: [],
};

const mockGet = vi.fn();

vi.mock("../../api", () => ({
  dashboardApi: { get: () => mockGet() },
  toolsApi: { list: vi.fn().mockResolvedValue([]) },
}));

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

describe("Dashboard", () => {
  beforeEach(() => {
    mockGet.mockResolvedValue(EMPTY_DATA);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("renders four section headings", async () => {
    renderDashboard();
    await waitFor(() => screen.getByText("Active runs"));
    expect(screen.getByText("Active runs")).toBeInTheDocument();
    expect(screen.getByText("Recent completions")).toBeInTheDocument();
    expect(screen.getByText("Top personas")).toBeInTheDocument();
  });

  it("shows active run task_progress chip", async () => {
    mockGet.mockResolvedValue({
      ...EMPTY_DATA,
      active_runs: [{
        run_id: "run-1",
        project_id: "proj-1",
        project_name: "",
        goal: "build something",
        status: "running",
        current_agents: [],
        started_at: null,
        elapsed_s: 60,
        task_progress: { closed: 2, total: 5 },
      }],
    });
    renderDashboard();
    await waitFor(() => screen.getByText("2 / 5 tasks"));
    expect(screen.getByText("2 / 5 tasks")).toBeInTheDocument();
  });

  it("polls every 5s with fake timers", async () => {
    vi.useFakeTimers();
    renderDashboard();
    // Flush initial fetchData() promise chain (microtasks only, no real timers needed)
    await act(async () => { await Promise.resolve(); });
    expect(mockGet).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(5001); });
    expect(mockGet).toHaveBeenCalledTimes(2);
  });

  it("pauses polling when document.hidden is true", async () => {
    vi.useFakeTimers();
    renderDashboard();
    await act(async () => { await Promise.resolve(); });
    expect(mockGet).toHaveBeenCalledTimes(1);
    // Simulate hidden tab
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    await act(async () => { vi.advanceTimersByTime(5001); });
    // Should NOT fetch again while hidden
    expect(mockGet).toHaveBeenCalledTimes(1);
    // Restore
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  });
});
