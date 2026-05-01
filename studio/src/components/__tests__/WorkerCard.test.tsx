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
  it("renders role, model tier chip, and gear icon", () => {
    const onOpenSettings = vi.fn();
    render(
      <WorkerCard
        agent={baseAgent}
        allRoles={["developer", "reviewer"]}
        mode="config"
        onChange={() => {}}
        onRemove={() => {}}
        onOpenSettings={onOpenSettings}
      />,
    );
    // Role name visible
    expect(screen.getByText("developer")).toBeInTheDocument();
    // Model tier chip
    expect(screen.getByText("solid")).toBeInTheDocument();
    // Model dropdown still present inline (most-changed field kept on card)
    expect(screen.getByDisplayValue("Solid (claude-sonnet-4-6)")).toBeInTheDocument();
    // Gear icon present
    expect(screen.getByTitle("Open agent settings")).toBeInTheDocument();
  });

  it("clicking gear triggers onOpenSettings", () => {
    const onOpenSettings = vi.fn();
    render(
      <WorkerCard
        agent={baseAgent}
        allRoles={[]}
        mode="config"
        onChange={() => {}}
        onRemove={() => {}}
        onOpenSettings={onOpenSettings}
      />,
    );
    fireEvent.click(screen.getByTitle("Open agent settings"));
    expect(onOpenSettings).toHaveBeenCalled();
  });

  it("description and system prompt are NOT shown in config card body (moved to slide-over)", () => {
    render(
      <WorkerCard
        agent={baseAgent}
        allRoles={[]}
        mode="config"
        onChange={() => {}}
        onRemove={() => {}}
      />,
    );
    // The description input field should not be in the card
    expect(screen.queryByDisplayValue("Writes the code")).toBeNull();
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

  it("model-tier change in card fires onChange", () => {
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
    const select = screen.getByDisplayValue("Solid (claude-sonnet-4-6)");
    fireEvent.change(select, { target: { value: "fast" } });
    expect(onChange).toHaveBeenCalledWith({ model_tier: "fast" });
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
