import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
      { id: "mistral-large-latest", provider: "mistral", tier: "heavy", supports_tools: true, deprecated: false },
      { id: "mistral-medium-latest", provider: "mistral", tier: "solid", supports_tools: true, deprecated: false },
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
    <QueryClientProvider client={new QueryClient()}><WorkerSettingsPanel
      agent={agent}
      allRoles={["developer", "reviewer"]}
      projectRbacScopes={["code-exec"]}
      onClose={onClose}
      onChange={onChange}
      onSave={onSave}
      onRbacChange={onRbacChange}
      {...overrides}
    /></QueryClientProvider>,
  );
  return { onChange, onSave, onClose, onRbacChange };
}

describe("WorkerSettingsPanel", () => {
  it("opens and shows agent role", () => {
    renderPanel();
    expect(screen.getByText(/Agent settings — developer/)).toBeInTheDocument();
  });

  it("closes on backdrop click", async () => {
    const { onClose } = renderPanel();
    // Radix registers its outside-pointer listener on the next tick.
    await new Promise((r) => setTimeout(r, 10));
    const overlay = document.querySelector("[data-state=open].fixed.inset-0") as HTMLElement;
    expect(overlay).not.toBeNull();
    fireEvent.pointerDown(overlay);
    fireEvent.mouseDown(overlay);
    fireEvent.click(overlay);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("closes on Escape key", () => {
    const { onClose } = renderPanel();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("switching provider to mistral persists model_id (no revert to anthropic)", async () => {
    // Regression: ModelPicker used to drop model_id, so on save the project
    // round-tripped to "first heavy model" = Anthropic Opus, making the UI
    // appear to revert. Both fields must travel together.
    const { onChange } = renderPanel();
    const providerSelect = await waitFor(() => screen.getByLabelText("Provider"));
    fireEvent.change(providerSelect, { target: { value: "mistral" } });
    // Provider change auto-picks the same-tier model in the new provider —
    // here that's mistral-medium-latest (solid).
    expect(onChange).toHaveBeenCalledWith({
      model_tier: "solid",
      model_id: "mistral-medium-latest",
    });
  });

  it("hydrates provider from saved model_id (not the tier fallback)", async () => {
    // If the agent has model_id="mistral-large-latest" the provider
    // dropdown must show "mistral", not "anthropic" (which would happen
    // if the picker hydrated from tier alone — first heavy = Opus).
    renderPanel({
      agent: {
        ...agent,
        model_tier: "heavy",
        model_id: "mistral-large-latest",
      },
    });
    const providerSelect = (await waitFor(() =>
      screen.getByLabelText("Provider"),
    )) as HTMLSelectElement;
    expect(providerSelect.value).toBe("mistral");
    const modelSelect = (await waitFor(() =>
      screen.getByLabelText("Model"),
    )) as HTMLSelectElement;
    expect(modelSelect.value).toBe("mistral-large-latest");
  });

  it("editing model fires onChange with both model_tier AND model_id", async () => {
    const { onChange } = renderPanel();
    // Wait for the async model picker to load
    const modelSelect = await waitFor(() => screen.getByLabelText("Model"));
    fireEvent.change(modelSelect, { target: { value: "claude-haiku-4-5-20251001" } });
    // Both fields must persist or the save round-trips back to first-tier-match.
    expect(onChange).toHaveBeenCalledWith({
      model_tier: "fast",
      model_id: "claude-haiku-4-5-20251001",
    });
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

  it("baseline tools render checked but un-locked; clicking writes to excluded_baseline_tools", async () => {
    // Track 12 — baseline tools are now toggleable. Default state is
    // checked (granted by default). Clicking adds the scope to the
    // excluded_baseline_tools list (does NOT touch declared_local_tools).
    const { onChange } = renderPanel({
      agent: { ...agent, declared_local_tools: [] },
    });
    const fsGroupHeader = await waitFor(() => screen.getByText("File System"));
    fireEvent.click(fsGroupHeader);

    await waitFor(() => {
      const checkboxes = document.querySelectorAll<HTMLInputElement>("input[type=checkbox]");
      const baselineBox = [...checkboxes].find((cb) =>
        cb.closest("label")?.textContent?.includes("fs-read"),
      );
      expect(baselineBox).toBeTruthy();
      // Baseline starts checked — granted by default.
      expect(baselineBox!.checked).toBe(true);
      // Track 12 fix — no longer disabled. Users can opt this agent out.
      expect(baselineBox!.disabled).toBe(false);
    });
    const baselineBox = [...document.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find(
      (cb) => cb.closest("label")?.textContent?.includes("fs-read"),
    )!;
    fireEvent.click(baselineBox);
    // Click writes to excluded_baseline_tools (not declared_local_tools).
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ excluded_baseline_tools: ["fs-read"] }),
    );
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
      <QueryClientProvider client={new QueryClient()}><WorkerSettingsPanel
        agent={agent}
        allRoles={[]}
        projectRbacScopes={[]}
        onClose={() => {}}
        onChange={onChange}
        onSave={onSave}
      /></QueryClientProvider>,
    );
    const modelSelect = await waitFor(() => screen.getByLabelText("Model"));
    fireEvent.change(modelSelect, { target: { value: "claude-haiku-4-5-20251001" } });
    // First call: optimistic update with new value (both fields).
    expect(onChange).toHaveBeenNthCalledWith(1, {
      model_tier: "fast",
      model_id: "claude-haiku-4-5-20251001",
    });
    // After save fails, revert call fires
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(2));
    // Second call reverts both fields to their original values.
    expect(onChange).toHaveBeenNthCalledWith(2, {
      model_tier: "solid",
      model_id: undefined,
    });
  });
});
