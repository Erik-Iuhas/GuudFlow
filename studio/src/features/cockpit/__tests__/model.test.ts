import { describe, expect, it } from "vitest";
import {
  buildGraph, computeBudget, formatElapsed, formatUsd, interventionTarget, mergeRoles, parseTimestamp,
  reviewLabel, roleSpecs,
} from "../model";
import type { AgentView } from "../../../stores/runEvents";

const agent = (role: string, state: AgentView["state"]): AgentView => ({ role, state, spawns: 1, toolCalls: 0, cost: 0 });

const specs = roleSpecs([
  { role: "planner" },
  { role: "dev", depends_on: ["planner"] },
  { role: "reviewer", depends_on: ["dev"], review_of: ["dev"], max_review_rounds: 3 },
  { role: "overseer" },
]);

describe("buildGraph", () => {
  it("layers by depends_on and parks the overseer detached with no edges", () => {
    const g = buildGraph(specs, { overseer: agent("overseer", "running") }, {}, {});
    const x = (id: string) => g.nodes.find((n) => n.id === id)?.x;
    expect(x("planner")).toBe(0);
    expect(x("dev")).toBeGreaterThan(x("planner") as number);
    expect(x("reviewer")).toBeGreaterThan(x("dev") as number);
    const ov = g.nodes.find((n) => n.id === "overseer");
    expect(ov?.detached).toBe(true);
    expect(ov?.pulsing).toBe(true);
    expect(g.edges.some((e) => e.source === "overseer" || e.target === "overseer")).toBe(false);
  });

  it("adds depends_on edges and a review edge reviewer -> author with the round label", () => {
    const g = buildGraph(specs, {}, { "reviewer>dev": { reviewer: "reviewer", author: "dev", round: 2 } }, {});
    expect(g.edges.filter((e) => e.kind === "depends_on").map((e) => e.id)).toEqual([
      "dep:planner>dev", "dep:dev>reviewer",
    ]);
    const review = g.edges.find((e) => e.kind === "review");
    expect(review).toMatchObject({ source: "reviewer", target: "dev", label: "round 2/3" });
  });

  it("draws a dashed blocked edge only between known roles", () => {
    const blocked = {
      dev: { role: "dev", on: "planner", since: "t" },
      ghost: { role: "ghost", on: "planner", since: "t" },
    };
    const g = buildGraph(specs, {}, {}, blocked);
    const edges = g.edges.filter((e) => e.kind === "blocked");
    expect(edges).toHaveLength(1);
    expect(edges[0]).toMatchObject({ source: "dev", target: "planner" });
  });

  it("does not pulse the overseer when idle", () => {
    const g = buildGraph(specs, { overseer: agent("overseer", "complete") }, {}, {});
    expect(g.nodes.find((n) => n.id === "overseer")?.pulsing).toBe(false);
  });

  it("survives a dependency cycle", () => {
    const cyc = roleSpecs([{ role: "a", depends_on: ["b"] }, { role: "b", depends_on: ["a"] }]);
    expect(buildGraph(cyc, {}, {}, {}).nodes).toHaveLength(2);
  });
});

describe("mergeRoles", () => {
  it("adds roles seen only on the board, skipping the runner", () => {
    const merged = mergeRoles(specs, ["dev", "extra", "runner"]);
    expect(merged.map((s) => s.role)).toContain("extra");
    expect(merged.map((s) => s.role)).not.toContain("runner");
    expect(merged.filter((s) => s.role === "dev")).toHaveLength(1);
  });
});

describe("labels", () => {
  it("formats review rounds", () => {
    expect(reviewLabel(1, 3)).toBe("round 1/3");
    expect(reviewLabel(2, null)).toBe("round 2");
    expect(reviewLabel(0, 3)).toBe("round 0/3");
  });
  it("formats elapsed time", () => {
    expect(formatElapsed(75)).toBe("1:15");
    expect(formatElapsed(3725)).toBe("1:02:05");
    expect(formatElapsed(-4)).toBe("0:00");
  });
  it("treats naive timestamps as UTC", () => {
    expect(parseTimestamp("2026-01-01T00:00:00")).toBe(Date.parse("2026-01-01T00:00:00Z"));
    expect(parseTimestamp("2026-01-01T00:00:00+02:00")).toBe(Date.parse("2026-01-01T00:00:00+02:00"));
    expect(parseTimestamp(undefined)).toBeNull();
  });
  it("names an intervention's target", () => {
    expect(interventionTarget({ type: "reassign", rationale: "r", needs_human: false, from_role: "a", to_role: "b", task_id: "T1" }))
      .toBe("a -> b · card T1");
    expect(interventionTarget({ type: "pause", rationale: "r", needs_human: false })).toBe("run");
  });
});

describe("computeBudget", () => {
  it("sums spend, ranks agents and computes the fraction", () => {
    const b = computeBudget({ a: 1, b: 3, c: 0 }, 8);
    expect(b.spent).toBe(4);
    expect(b.fraction).toBe(0.5);
    expect(b.remaining).toBe(4);
    expect(b.level).toBe("ok");
    expect(b.perAgent.map((p) => p.role)).toEqual(["b", "a"]);
    expect(b.perAgent[0].share).toBeCloseTo(0.75);
  });
  it("warns at 80% and flags over budget at 100%", () => {
    expect(computeBudget({ a: 4 }, 5).level).toBe("warn");
    const over = computeBudget({ a: 6 }, 5);
    expect(over.level).toBe("over");
    expect(over.remaining).toBe(0);
  });
  it("has no level without a cap", () => {
    for (const cap of [null, undefined, 0]) {
      const b = computeBudget({ a: 2 }, cap);
      expect(b).toMatchObject({ cap: null, fraction: null, level: "none", spent: 2 });
    }
  });
  it("formats small amounts with four decimals", () => {
    expect(formatUsd(0.0123)).toBe("$0.0123");
    expect(formatUsd(1.5)).toBe("$1.50");
  });
});
