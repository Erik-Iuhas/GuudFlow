import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WorkerCard } from "../WorkerCard";
import { avatarFor } from "../../lib/avatar";
import type { AgentConfig } from "../../types";

const baseAgent: AgentConfig = {
  role: "developer",
  description: "Writes the code",
  model_tier: "solid",
  declared_local_tools: ["fs-read", "fs-write"],
  depends_on: [],
  is_reviewer: false,
  max_tool_iterations: 6,
};

describe("WorkerCard — config mode", () => {
  it("renders all editable fields", () => {
    render(
      <WorkerCard
        agent={baseAgent}
        allRoles={["developer", "reviewer"]}
        mode="config"
        onChange={() => {}}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByDisplayValue("Writes the code")).toBeInTheDocument();
    // Model select: display value is the option label text
    expect(screen.getByDisplayValue("Solid (claude-sonnet-4-6)")).toBeInTheDocument();
    // Tools render as chips via ToolsMultiSelect
    expect(screen.getByText("fs-read")).toBeInTheDocument();
    expect(screen.getByText("fs-write")).toBeInTheDocument();
    // depends_on checkbox — allRoles has "reviewer" which != agent.role
    expect(screen.getByText("reviewer")).toBeInTheDocument();
  });

  it("fires onChange when description is edited", () => {
    const onChange = vi.fn();
    render(
      <WorkerCard
        agent={baseAgent}
        allRoles={[]}
        mode="config"
        onChange={onChange}
        onRemove={() => {}}
      />,
    );
    const input = screen.getByDisplayValue("Writes the code");
    fireEvent.change(input, { target: { value: "New description" } });
    expect(onChange).toHaveBeenCalledWith({ description: "New description" });
  });

  it("fires onRemove when × is clicked", () => {
    const onRemove = vi.fn();
    render(
      <WorkerCard
        agent={baseAgent}
        allRoles={[]}
        mode="config"
        onChange={() => {}}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByTitle("remove"));
    expect(onRemove).toHaveBeenCalled();
  });
});

describe("WorkerCard — preview mode", () => {
  it("renders read-only content", () => {
    render(
      <WorkerCard agent={baseAgent} allRoles={[]} mode="preview" />,
    );
    expect(screen.getByText("Writes the code")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("does not crash when onChange is absent", () => {
    expect(() =>
      render(<WorkerCard agent={baseAgent} allRoles={[]} mode="preview" />),
    ).not.toThrow();
  });
});

describe("avatarFor", () => {
  it("is deterministic — same seed → same gradient", () => {
    const a1 = avatarFor("developer");
    const a2 = avatarFor("developer");
    expect(a1.gradient).toBe(a2.gradient);
    expect(a1.initials).toBe(a2.initials);
  });

  it("different seeds produce different gradients", () => {
    expect(avatarFor("alice").gradient).not.toBe(avatarFor("bob").gradient);
  });

  it("initials from hyphenated seed", () => {
    expect(avatarFor("tech-lead").initials).toBe("TL");
  });
});
