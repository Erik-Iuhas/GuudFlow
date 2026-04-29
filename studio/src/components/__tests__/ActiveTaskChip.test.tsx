import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ActiveTaskChip } from "../ActiveTaskChip";

const mockDesks = vi.fn();

vi.mock("../../api", () => ({
  runsApi: { desks: () => mockDesks() },
}));

// useDesks uses RunWsContext which defaults to null (no provider = polling fallback)
// We stub via api mock so no WS is needed.

describe("ActiveTaskChip", () => {
  it("shows IN_PROGRESS task goal (truncated to 60 chars)", async () => {
    mockDesks.mockResolvedValue({
      desks: {
        developer: [
          {
            task_id: "t1", story_id: "s1", parent_task_id: null,
            assigned_to: "developer", created_by: "planner",
            created_at_iter: 1,
            goal: "Build a comprehensive authentication system with OAuth support",
            acceptance_criteria: [], out_of_scope: [], references: [],
            status: "IN_PROGRESS", started_at_iter: 1,
            completed_at_iter: null, closed_by_commit: null,
          },
        ],
      },
      tasks_by_id: {},
    });
    render(<ActiveTaskChip runId="run-1" role="developer" />);
    await waitFor(() => {
      const btn = screen.getByRole("button");
      expect(btn.textContent).toMatch(/Build a comprehensive authentication system with OAuth/);
      // 60 chars + ellipsis
      expect(btn.textContent!.length).toBeLessThanOrEqual(62);
    });
  });

  it("falls back to PENDING task when no IN_PROGRESS", async () => {
    mockDesks.mockResolvedValue({
      desks: {
        developer: [
          {
            task_id: "t2", story_id: "s1", parent_task_id: null,
            assigned_to: "developer", created_by: "planner",
            created_at_iter: 1, goal: "Write unit tests",
            acceptance_criteria: [], out_of_scope: [], references: [],
            status: "PENDING", started_at_iter: null,
            completed_at_iter: null, closed_by_commit: null,
          },
        ],
      },
      tasks_by_id: {},
    });
    render(<ActiveTaskChip runId="run-1" role="developer" />);
    await waitFor(() => {
      expect(screen.getByRole("button").textContent).toBe("Write unit tests");
    });
  });

  it("shows idle when no tasks for the role", async () => {
    mockDesks.mockResolvedValue({ desks: {}, tasks_by_id: {} });
    render(<ActiveTaskChip runId="run-1" role="developer" />);
    await waitFor(() => {
      expect(screen.getByText("idle")).toBeInTheDocument();
    });
  });
});
