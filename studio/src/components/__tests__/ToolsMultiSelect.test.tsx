import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ToolsMultiSelect } from "../ToolsMultiSelect";

// Mock ../api so we don't hit a real server
vi.mock("../../api", () => ({
  toolsApi: {
    list: vi.fn().mockResolvedValue([
      {
        name: "fs-read",
        scope: "fs-read",
        description: "Read a file",
        baseline: true,
      },
      {
        name: "assign-task",
        scope: "assign-task",
        description: "Assign a task to an agent",
        baseline: false,
        granted_by_archetypes: ["architect", "requirements_writer", "tech_lead"],
      },
      {
        name: "fs-write",
        scope: "fs-write",
        description: "Write a file",
        baseline: false,
        granted_by_archetypes: [],
      },
    ]),
  },
}));

describe("ToolsMultiSelect", () => {
  it("shows assign-task disabled with tooltip for non-planner", async () => {
    render(
      <ToolsMultiSelect
        selected={[]}
        onChange={() => {}}
        archetypeRoleType="backend_dev"
        projectRbacScopes={[]}
      />,
    );
    // Open dropdown
    fireEvent.focus(screen.getByPlaceholderText("Add tools…"));
    await waitFor(() => screen.getByText("assign-task"));

    const item = screen.getByText("assign-task").closest("div")!;
    expect(item).toHaveAttribute("title");
    expect(item.getAttribute("title")).toContain("Requires scope assign-task");
    // Should be visually disabled (cursor: not-allowed via style)
    expect(item.style.cursor).toBe("not-allowed");
  });

  it("shows assign-task enabled for architect archetype", async () => {
    render(
      <ToolsMultiSelect
        selected={[]}
        onChange={() => {}}
        archetypeRoleType="architect"
        projectRbacScopes={[]}
      />,
    );
    fireEvent.focus(screen.getByPlaceholderText("Add tools…"));
    await waitFor(() => screen.getByText("assign-task"));

    const item = screen.getByText("assign-task").closest("div")!;
    expect(item.style.cursor).toBe("pointer");
  });

  it("selecting a tool calls onChange with new tool appended", async () => {
    const onChange = vi.fn();
    render(
      <ToolsMultiSelect
        selected={["fs-read"]}
        onChange={onChange}
        archetypeRoleType="architect"
        projectRbacScopes={[]}
      />,
    );
    fireEvent.focus(screen.getByPlaceholderText(""));
    await waitFor(() => screen.getByText("assign-task"));
    fireEvent.click(screen.getByText("assign-task").closest("div")!);
    expect(onChange).toHaveBeenCalledWith(["fs-read", "assign-task"]);
  });

  it("removing a chip calls onChange with tool removed", async () => {
    const onChange = vi.fn();
    render(
      <ToolsMultiSelect
        selected={["fs-read", "assign-task"]}
        onChange={onChange}
        archetypeRoleType="architect"
        projectRbacScopes={[]}
      />,
    );
    // Find the × button for fs-read chip
    const removeBtn = screen.getByLabelText("Remove fs-read");
    fireEvent.click(removeBtn);
    expect(onChange).toHaveBeenCalledWith(["assign-task"]);
  });
});
