import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { KindPanel } from "../KindPanel";
import type { ProjectConfig } from "../../../types";

vi.mock("../../design/api", () => ({
  useKinds: () => ({ data: { kinds: [
    { id: "game", title: "A game", purpose: "", start_modes: ["spec_first", "adopt"], default_start_mode: "spec_first",
      lead_contract: "tech_lead", team_template: [] }] } }),
}));

const project = { project_id: "p", goal: "g", agents: [], start_mode: "spec_first",
  team_warnings: ["planner and tech_lead both plan sessions"] } as unknown as ProjectConfig;

describe("KindPanel", () => {
  it("sets kind and start mode and shows team warnings", () => {
    const onChange = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><KindPanel project={project} onChange={onChange} /></QueryClientProvider>);
    expect(screen.getByTestId("team-warnings").textContent).toContain("both plan sessions");
    fireEvent.change(screen.getByLabelText("Project kind"), { target: { value: "game" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "game", start_mode: "spec_first" }));
    fireEvent.change(screen.getByLabelText("Start mode"), { target: { value: "adopt" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ start_mode: "adopt" }));
  });
});
