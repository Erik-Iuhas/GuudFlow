import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { normalizeTab } from "../projectTabs";
import { detailKey, useDetailLevel } from "../useDetailLevel";

const mocks = vi.hoisted(() => ({ get: vi.fn(), runs: vi.fn() }));

vi.mock("../../../api", () => {
  class ApiError extends Error {
    constructor(public status: number, public detail: string) {
      super(`${status}: ${detail}`);
    }
  }
  return {
    ApiError,
    invokeApi: { invoke: vi.fn() },
    projectsApi: { get: mocks.get, update: vi.fn() },
    runsApi: { list: mocks.runs },
  };
});
vi.mock("../../work/ProjectWork", () => ({ ProjectWork: () => <div>work content</div> }));
vi.mock("../../../components/RoadmapTab", () => ({ RoadmapTab: () => <div>roadmap content</div> }));
vi.mock("../../../views/Inputs", () => ({ Inputs: () => <div>inputs content</div> }));
vi.mock("../ProjectInsights", () => ({ ProjectInsights: () => <div>insights content</div> }));
vi.mock("../../plan/PlanTab", () => ({ PlanTab: () => <div>plan conversation</div> }));
vi.mock("../../../components/CostStrip", () => ({ CostStrip: () => <div /> }));
vi.mock("../../../components/PipelineGraph", () => ({ PipelineGraph: () => <div /> }));
vi.mock("../../../components/ResumeButton", () => ({ ResumeButton: () => <button>Resume</button> }));
vi.mock("../../../components/WorkerSettingsPanel", () => ({ WorkerSettingsPanel: () => <div /> }));
vi.mock("../../../components/PersonaSlideOver", () => ({ PersonaSlideOver: () => <div /> }));
vi.mock("../../work/WorkSettings", () => ({ WorkSettings: () => <div /> }));

import { Studio } from "../../../views/Studio";

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("normalizeTab", () => {
  it("keeps current names and maps the old ones", () => {
    for (const t of ["work", "plan", "sessions", "team", "record", "settings"]) expect(normalizeTab(t)).toBe(t);
    expect(normalizeTab("inputs")).toBe("plan");
    expect(normalizeTab("roadmap")).toBe("plan");
    expect(normalizeTab("insights")).toBe("record");
    expect(normalizeTab("nonsense")).toBe("work");
    expect(normalizeTab(null)).toBe("work");
  });
});

describe("useDetailLevel", () => {
  it("defaults to overview, persists per project and reads it back", () => {
    const { result, unmount } = renderHook(() => useDetailLevel("p1"));
    expect(result.current[0]).toBe("overview");
    act(() => result.current[1]("technical"));
    expect(result.current[0]).toBe("technical");
    expect(window.localStorage.getItem(detailKey("p1"))).toBe("technical");
    unmount();
    expect(renderHook(() => useDetailLevel("p1")).result.current[0]).toBe("technical");
    expect(renderHook(() => useDetailLevel("p2")).result.current[0]).toBe("overview");
  });

  it("survives blocked storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { result } = renderHook(() => useDetailLevel("p1"));
    expect(result.current[0]).toBe("overview");
    act(() => result.current[1]("technical"));
    expect(result.current[0]).toBe("technical");
  });
});

function renderStudio(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/projects/:id" element={<Studio />} />
      </Routes>
    </MemoryRouter>,
  );
}

const PROJECT = {
  project_id: "p1",
  goal: "g",
  agents: [{ role: "dev", model_tier: "solid" }],
  seats: { lead: true, reviewer: "all" },
};

describe("Studio project page", () => {
  it("has the six tabs, Work first and selected by default", async () => {
    mocks.get.mockResolvedValue(PROJECT);
    mocks.runs.mockResolvedValue([]);
    renderStudio("/projects/p1");
    expect(await screen.findByText("work content")).toBeTruthy();
    const names = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(names).toEqual(["Work", "Plan", "Sessions", "Team", "Record", "Settings"]);
    expect(screen.getByRole("tab", { name: "Work" }).getAttribute("aria-selected")).toBe("true");
  });

  it.each([
    ["inputs", "Plan", "inputs content"],
    ["roadmap", "Plan", "roadmap content"],
    ["insights", "Record", "insights content"],
    ["plan", "Plan", "roadmap content"],
    ["sessions", "Sessions", "No sessions yet"],
  ])("?tab=%s opens %s", async (param, tab, text) => {
    mocks.get.mockResolvedValue(PROJECT);
    mocks.runs.mockResolvedValue([]);
    renderStudio(`/projects/p1?tab=${param}`);
    expect(await screen.findByText(new RegExp(text))).toBeTruthy();
    expect(screen.getByRole("tab", { name: tab }).getAttribute("aria-selected")).toBe("true");
  });

  it("Plan shows Notes and Files; clicking a tab switches content", async () => {
    mocks.get.mockResolvedValue(PROJECT);
    mocks.runs.mockResolvedValue([]);
    renderStudio("/projects/p1?tab=plan");
    expect(await screen.findByRole("heading", { name: "Notes" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Files" })).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Record" }), { button: 0 });
    expect(await screen.findByText("insights content")).toBeTruthy();
  });

  it("Sessions lists runs as Session N, oldest first, newest on top", async () => {
    mocks.get.mockResolvedValue(PROJECT);
    mocks.runs.mockResolvedValue([
      { run_id: "run_b", project_id: "p1", status: "complete", created_at: "2026-02-01T10:00:00" },
      { run_id: "run_a", project_id: "p1", status: "failed", created_at: "2026-01-01T10:00:00" },
    ]);
    renderStudio("/projects/p1?tab=sessions");
    const first = await screen.findByText("Session 2");
    expect(first).toBeTruthy();
    expect(screen.getByText("run_a").closest("td")?.textContent).toContain("Session 1");
    expect(screen.getByText("run_b").closest("td")?.textContent).toContain("Session 2");
    const rows = screen.getAllByRole("row");
    expect(rows[1].textContent).toContain("Session 2");
  });

  it("Team tab has no sub-LLM section and no recent runs table", async () => {
    mocks.get.mockResolvedValue(PROJECT);
    mocks.runs.mockResolvedValue([]);
    renderStudio("/projects/p1?tab=team");
    expect(await screen.findByText("Goal")).toBeTruthy();
    expect(screen.queryByText(/Sub-LLM/)).toBeNull();
    expect(screen.queryByText("Recent runs")).toBeNull();
    expect(document.body.textContent?.toLowerCase()).not.toContain("legacy");
  });

  it("header shows seat chips from project.seats", async () => {
    mocks.get.mockResolvedValue(PROJECT);
    mocks.runs.mockResolvedValue([]);
    renderStudio("/projects/p1");
    const chips = await screen.findByTestId("seat-chips");
    expect(chips.textContent).toBe("OwnerLeadReviewer");
  });

  it("header shows only Owner when no seats are set", async () => {
    mocks.get.mockResolvedValue({ ...PROJECT, seats: undefined });
    mocks.runs.mockResolvedValue([]);
    renderStudio("/projects/p1");
    expect((await screen.findByTestId("seat-chips")).textContent).toBe("Owner");
  });

  it("the Overview / Technical switch is stored under guudflow.detail.<id>", async () => {
    mocks.get.mockResolvedValue(PROJECT);
    mocks.runs.mockResolvedValue([]);
    const { unmount } = renderStudio("/projects/p1");
    const tech = await screen.findByRole("button", { name: "Technical" });
    expect(screen.getByRole("button", { name: "Overview" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(tech);
    expect(tech.getAttribute("aria-pressed")).toBe("true");
    expect(window.localStorage.getItem("guudflow.detail.p1")).toBe("technical");
    unmount();
    renderStudio("/projects/p1");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Technical" }).getAttribute("aria-pressed")).toBe("true"),
    );
  });
});
