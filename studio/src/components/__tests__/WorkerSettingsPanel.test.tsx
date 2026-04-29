import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { WorkerSettingsPanel } from "../WorkerSettingsPanel";
import type { AgentConfig } from "../../types";

// Mock toolsApi so RBAC section doesn't hang on network
vi.mock("../../api", () => ({
  toolsApi: {
    list: vi.fn().mockResolvedValue([
      { name: "fs-read", scope: "fs-read", description: "Read", baseline: true },
      { name: "fs-write", scope: "fs-write", description: "Write", baseline: false },
    ]),
  },
}));

const agent: AgentConfig = {
  role: "developer",
  description: "Writes code",
  model_tier: "solid",
  declared_local_tools: ["fs-read"],
  depends_on: [],
  is_reviewer: false,
  max_tool_iterations: 6,
};

function renderPanel(overrides: Partial<Parameters<typeof WorkerSettingsPanel>[0]> = {}) {
  const onChange = vi.fn();
  const onSave = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(
    <WorkerSettingsPanel
      agent={agent}
      allRoles={["developer", "reviewer"]}
      projectRbacScopes={["code-exec"]}
      onClose={onClose}
      onChange={onChange}
      onSave={onSave}
      {...overrides}
    />,
  );
  return { onChange, onSave, onClose };
}

describe("WorkerSettingsPanel", () => {
  it("opens and shows agent role", () => {
    renderPanel();
    expect(screen.getByText(/Agent settings — developer/)).toBeInTheDocument();
  });

  it("closes on backdrop click", () => {
    const { onClose } = renderPanel();
    const backdrop = document.querySelector(".slide-over-backdrop")!;
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape key", () => {
    const { onClose } = renderPanel();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("editing model tier fires onChange with model_tier", async () => {
    const { onChange } = renderPanel();
    const select = screen.getByDisplayValue("Solid");
    fireEvent.change(select, { target: { value: "fast" } });
    expect(onChange).toHaveBeenCalledWith({ model_tier: "fast" });
  });

  it("RBAC section lists baseline scopes under 'from baseline'", async () => {
    renderPanel();
    await waitFor(() => {
      // Find the <li> in the RBAC section that contains "from baseline"
      expect(screen.queryAllByText("from baseline").length).toBeGreaterThan(0);
    });
    const baselineItems = screen.getAllByText("from baseline");
    // There should be at least one baseline item (fs-read)
    expect(baselineItems.length).toBeGreaterThan(0);
    // The parent li should contain a scope code element
    const li = baselineItems[0].closest("li")!;
    expect(li.textContent).toMatch(/fs-/);
  });

  it("RBAC section lists project scopes under 'from project'", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getAllByText("code-exec").length).toBeGreaterThan(0);
    });
    // The code-exec scope from projectRbacScopes should show "from project"
    const items = screen.getAllByText("code-exec");
    const inRbac = items.find((el) =>
      el.closest("li")?.textContent?.includes("from project"),
    );
    expect(inRbac).toBeTruthy();
  });

  it("optimistic save: rejects network → field reverts", async () => {
    const onChange = vi.fn();
    const onSave = vi.fn().mockRejectedValue(new Error("Network error"));
    render(
      <WorkerSettingsPanel
        agent={agent}
        allRoles={[]}
        projectRbacScopes={[]}
        onClose={() => {}}
        onChange={onChange}
        onSave={onSave}
      />,
    );
    const select = screen.getByDisplayValue("Solid");
    fireEvent.change(select, { target: { value: "fast" } });
    // First call: optimistic update with new value
    expect(onChange).toHaveBeenNthCalledWith(1, { model_tier: "fast" });
    // After save fails, revert call fires
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2));
    // Second call reverts to original
    expect(onChange).toHaveBeenNthCalledWith(2, { model_tier: "solid" });
  });
});
