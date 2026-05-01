import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { WorkerSettingsPanel } from "../WorkerSettingsPanel";
import type { AgentConfig } from "../../types";

// Mock toolsApi and modelsApi so components don't hang on network
vi.mock("../../api", () => ({
  toolsApi: {
    list: vi.fn().mockResolvedValue([
      { name: "fs-read", scope: "fs-read", description: "Read", baseline: true },
      { name: "fs-write", scope: "fs-write", description: "Write", baseline: false, granted_by_archetypes: [] },
    ]),
  },
  modelsApi: {
    list: vi.fn().mockResolvedValue([
      { id: "claude-sonnet-4-6", provider: "anthropic", tier: "solid", supports_tools: true, deprecated: false },
      { id: "claude-opus-4-7", provider: "anthropic", tier: "heavy", supports_tools: true, deprecated: false },
      { id: "claude-haiku-4-5-20251001", provider: "anthropic", tier: "fast", supports_tools: true, deprecated: false },
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
  const onRbacChange = vi.fn();
  render(
    <WorkerSettingsPanel
      agent={agent}
      allRoles={["developer", "reviewer"]}
      projectRbacScopes={["code-exec"]}
      onClose={onClose}
      onChange={onChange}
      onSave={onSave}
      onRbacChange={onRbacChange}
      {...overrides}
    />,
  );
  return { onChange, onSave, onClose, onRbacChange };
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
    // Wait for the async model picker to load
    const modelSelect = await waitFor(() => screen.getByLabelText("Model"));
    fireEvent.change(modelSelect, { target: { value: "claude-haiku-4-5-20251001" } });
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

  it("baseline tools render as checked and disabled regardless of declared_local_tools", async () => {
    // declared_local_tools is empty but baseline=true tools must still appear checked+disabled
    const { onChange } = renderPanel({
      agent: { ...agent, declared_local_tools: [] },
    });
    // Wait for the "File System" group header to appear (tools loaded)
    const fsGroupHeader = await waitFor(() => screen.getByText("File System"));
    // Expand the group
    fireEvent.click(fsGroupHeader);

    // Now fs-read checkbox should be visible, checked, and disabled
    await waitFor(() => {
      const checkboxes = document.querySelectorAll<HTMLInputElement>("input[type=checkbox]");
      const baselineBox = [...checkboxes].find((cb) =>
        cb.closest("label")?.textContent?.includes("fs-read"),
      );
      expect(baselineBox).toBeTruthy();
      expect(baselineBox!.checked).toBe(true);
      expect(baselineBox!.disabled).toBe(true);
    });
    // Clicking a baseline tool must NOT call onChange
    const baselineBox = [...document.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find(
      (cb) => cb.closest("label")?.textContent?.includes("fs-read"),
    )!;
    fireEvent.click(baselineBox);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("off-archetype tool check fires onChange AND onRbacChange with scope to add", async () => {
    const { onChange, onRbacChange } = renderPanel({
      agent: { ...agent, declared_local_tools: [], agent_class_role_type: "code_reviewer" },
      projectRbacScopes: [],
    });
    // Expand the File System group
    const fsGroupHeader = await waitFor(() => screen.getByText("File System"));
    fireEvent.click(fsGroupHeader);

    // fs-write is off-archetype for code_reviewer (not in granted_by_archetypes=[])
    await waitFor(() => {
      const cb = [...document.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find(
        (c) => c.closest("label")?.textContent?.includes("fs-write"),
      );
      expect(cb).toBeTruthy();
      expect(cb!.disabled).toBe(false);
    });
    const fsWriteBox = [...document.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find(
      (c) => c.closest("label")?.textContent?.includes("fs-write"),
    )!;
    fireEvent.click(fsWriteBox);

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ declared_local_tools: expect.arrayContaining(["fs-write"]) }));
      expect(onRbacChange).toHaveBeenCalledWith(["fs-write"], []);
    });
  });

  it("off-archetype tool uncheck fires onRbacChange with scope to remove", async () => {
    const { onRbacChange } = renderPanel({
      agent: { ...agent, declared_local_tools: ["fs-write"], agent_class_role_type: "code_reviewer" },
      projectRbacScopes: [],
    });
    const fsGroupHeader = await waitFor(() => screen.getByText("File System"));
    fireEvent.click(fsGroupHeader);

    await waitFor(() => {
      const cb = [...document.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find(
        (c) => c.closest("label")?.textContent?.includes("fs-write"),
      );
      expect(cb?.checked).toBe(true);
    });
    const fsWriteBox = [...document.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find(
      (c) => c.closest("label")?.textContent?.includes("fs-write"),
    )!;
    fireEvent.click(fsWriteBox);

    await waitFor(() => {
      expect(onRbacChange).toHaveBeenCalledWith([], ["fs-write"]);
    });
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
    const modelSelect = await waitFor(() => screen.getByLabelText("Model"));
    fireEvent.change(modelSelect, { target: { value: "claude-haiku-4-5-20251001" } });
    // First call: optimistic update with new value
    expect(onChange).toHaveBeenNthCalledWith(1, { model_tier: "fast" });
    // After save fails, revert call fires
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2));
    // Second call reverts to original
    expect(onChange).toHaveBeenNthCalledWith(2, { model_tier: "solid" });
  });
});
