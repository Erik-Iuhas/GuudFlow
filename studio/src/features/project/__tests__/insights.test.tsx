import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Sparkline } from "../Sparkline";
import { MetricsTrend, PlaybookView } from "../ProjectInsights";
import { groupPlaybook, seriesValues, sparklinePoints, sparklineSummary } from "../model";
import type { PlaybookEntryWire, RunMetricsPoint } from "../../report/api";

describe("sparkline", () => {
  it("renders one point per value with an accessible text summary", () => {
    render(<Sparkline label="Stall minutes" values={[4, 2, 1, 3]} />);
    const svg = screen.getByRole("img", { name: /Stall minutes: 4 runs, latest 3, up from 1, range 1 to 4/ });
    expect(svg.querySelectorAll("[data-testid=spark-point]")).toHaveLength(4);
    expect(svg.querySelector("polyline")).not.toBeNull();
  });

  it("draws a single run as a lone point and no run as nothing", () => {
    const { rerender } = render(<Sparkline label="Cost" values={[1.5]} />);
    expect(screen.getAllByTestId("spark-point")).toHaveLength(1);
    expect(document.querySelector("polyline")).toBeNull();
    rerender(<Sparkline label="Cost" values={[]} />);
    expect(screen.queryAllByTestId("spark-point")).toHaveLength(0);
    expect(screen.getByRole("img")).toHaveAccessibleName("Cost: no runs recorded yet.");
  });

  it("puts higher values higher on screen and keeps points inside the box", () => {
    const pts = sparklinePoints([0, 5, 10], 100, 30, 3);
    expect(pts[2]!.y).toBeLessThan(pts[0]!.y);
    for (const p of pts) {
      expect(p.x).toBeGreaterThanOrEqual(3);
      expect(p.x).toBeLessThanOrEqual(97);
      expect(p.y).toBeGreaterThanOrEqual(3);
      expect(p.y).toBeLessThanOrEqual(27);
    }
    expect(sparklinePoints([2, 2, 2], 100, 30)[1]!.y).toBe(15);
  });

  it("summarises direction", () => {
    expect(sparklineSummary("x", [3, 1])).toContain("down from 3");
    expect(sparklineSummary("x", [2, 2])).toContain("same as 2");
  });
});

const runs: RunMetricsPoint[] = [
  { run_id: "run_1", recorded_at: "2026-03-01", metrics: { stall_minutes: 12, interventions: 3, cost_usd: 1.2 } },
  { run_id: "run_2", recorded_at: "2026-03-02", metrics: { stall_minutes: 4, interventions: 1, cost_usd: 0.9 } },
  { run_id: "run_3", recorded_at: "2026-03-03", metrics: { stall_minutes: 1, interventions: 1 } },
];

describe("MetricsTrend", () => {
  it("labels every series in text and skips runs that lack a metric", () => {
    render(<MemoryRouter><MetricsTrend runs={runs} /></MemoryRouter>);
    const tiles = screen.getAllByTestId("metric");
    expect(tiles).toHaveLength(7);
    const stall = tiles.find((t) => t.textContent?.includes("Stall minutes")) as HTMLElement;
    expect(within(stall).getAllByTestId("spark-point")).toHaveLength(3);
    expect(stall).toHaveTextContent("Stall minutes: 3 runs, latest 1, down from 4");
    const cost = tiles.find((t) => t.textContent?.startsWith("Cost (USD)")) as HTMLElement;
    expect(within(cost).getAllByTestId("spark-point")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "run_3" })).toHaveAttribute("href", "/runs/run_3/report");
    expect(seriesValues(runs, "cost_usd").map((p) => p.runId)).toEqual(["run_1", "run_2"]);
  });

  it("has an empty state", () => {
    render(<MemoryRouter><MetricsTrend runs={[]} /></MemoryRouter>);
    expect(screen.getByText(/No finished run has recorded metrics/)).toBeInTheDocument();
  });
});

const entry = (over: Partial<PlaybookEntryWire>): PlaybookEntryWire => ({
  id: "e", section: "known_pitfalls", key: "dev", value: "v", evidence_runs: ["run_1"], confidence: 0.5, state: "emerging", ...over,
});

describe("playbook", () => {
  const entries = [
    entry({ id: "a", section: "known_pitfalls", value: "Hourly arrays are not objects", confidence: 0.55, state: "emerging" }),
    entry({ id: "b", section: "brief_addenda", key: "builder", value: "Read notes first", confidence: 0.8, state: "active", evidence_runs: ["run_1", "run_2"] }),
    entry({ id: "c", section: "known_pitfalls", value: "Old lesson", confidence: 0.9, state: "archived" }),
    entry({ id: "d", section: "known_pitfalls", value: "Best lesson", confidence: 0.75, state: "active" }),
    entry({ id: "f", section: "custom_future", value: "x" }),
    entry({ id: "t", section: "task_templates", value: "Implement A; Write tests for B; Verify C" }),
  ];

  it("groups by section in a fixed order, live states first then confidence", () => {
    const groups = groupPlaybook(entries);
    expect(groups.map((g) => g.section)).toEqual(["brief_addenda", "known_pitfalls", "custom_future"]); // no task_templates
    expect(groups[1]!.entries.map((e) => e.id)).toEqual(["d", "a", "c"]);
  });

  it("renders state badges, confidence bars and evidence links to the reports", () => {
    render(<MemoryRouter><PlaybookView entries={entries} /></MemoryRouter>);
    const brief = screen.getByTestId("playbook-brief_addenda");
    expect(brief).toHaveTextContent("Brief addenda (1)");
    expect(within(brief).getByText("active")).toBeInTheDocument();
    expect(within(brief).getByRole("img", { name: "confidence 80%" })).toBeInTheDocument();
    expect(within(brief).getByRole("link", { name: "run_2" })).toHaveAttribute("href", "/runs/run_2/report");
    expect(screen.getAllByTestId("playbook-entry")).toHaveLength(5);
  });

  it("has an empty state", () => {
    render(<MemoryRouter><PlaybookView entries={[]} /></MemoryRouter>);
    expect(screen.getByText(/playbook is empty/)).toBeInTheDocument();
  });
});
