import { describe, expect, it } from "vitest";
import type { TypedSignal } from "../../../lib/runEvents.gen";
import type { CardView } from "../../../stores/runEvents";
import {
  acceptanceState,
  blockedOn,
  buildLanes,
  columnCounts,
  computeReorder,
  formatAge,
  isAged,
  mergeRanges,
} from "../boardModel";

function cv(p: Partial<CardView> & { id: string }): CardView {
  return {
    title: p.id, owner: "dev", column: "todo", kind: "code", size: "S", priority: 0, round: 0,
    acceptance: [], thread: [], dependsOn: [], ...p,
  };
}

describe("computeReorder", () => {
  const cell = (...ps: number[]) => ps.map((priority, i) => ({ id: `T${i + 1}`, priority }));

  it("is a no-op when the position does not change", () => {
    expect(computeReorder(cell(2, 1, 0), "T2", 1)).toEqual({});
    expect(computeReorder(cell(1), "T1", 0)).toEqual({});
  });

  it("moving to the top goes one above the old top", () => {
    expect(computeReorder(cell(2, 1, 0), "T3", 0)).toEqual({ T3: 3 });
  });

  it("moving to the bottom goes one below the old bottom", () => {
    expect(computeReorder(cell(2, 1, 0), "T1", 2)).toEqual({ T1: -1 });
  });

  it("slots between neighbours with room", () => {
    expect(computeReorder(cell(10, 5, 0), "T1", 1)).toEqual({ T1: 2 });
  });

  it("renumbers the cell when neighbours leave no integer gap", () => {
    // [T1:0, T2:0, T3:0] with T3 moved between T1 and T2.
    expect(computeReorder(cell(0, 0, 0), "T3", 1)).toEqual({ T3: -1, T2: -2 });
  });

  it("clamps an out-of-range target", () => {
    expect(computeReorder(cell(2, 1), "T2", 99)).toEqual({});
    expect(computeReorder(cell(2, 1), "T2", -4)).toEqual({ T2: 3 });
  });
});

describe("buildLanes", () => {
  it("groups by owner then column, priority first, and keeps the team order", () => {
    const lanes = buildLanes(
      [
        cv({ id: "T1", owner: "tester", priority: 1 }),
        cv({ id: "T2", owner: "dev", column: "review", priority: 0 }),
        cv({ id: "T10", owner: "dev", column: "review", priority: 0 }),
        cv({ id: "T3", owner: "dev", column: "review", priority: 5 }),
        cv({ id: "T4", owner: "zed" }),
      ],
      ["dev", "tester"],
    );
    expect(lanes.map((l) => l.role)).toEqual(["dev", "tester", "zed"]);
    expect(lanes[0]?.cells.review.map((c) => c.id)).toEqual(["T3", "T2", "T10"]);
    expect(lanes[1]?.cells.todo.map((c) => c.id)).toEqual(["T1"]);
  });

  it("columnCounts totals across lanes", () => {
    const c = columnCounts([cv({ id: "a" }), cv({ id: "b", column: "done" }), cv({ id: "c" })]);
    expect(c.todo).toBe(2);
    expect(c.done).toBe(1);
  });
});

describe("age and blocked helpers", () => {
  const now = Date.parse("2026-01-01T12:00:00Z");
  const ago = (min: number) => new Date(now - min * 60_000).toISOString();

  it("formats ages", () => {
    expect(formatAge(undefined, now)).toBe("");
    expect(formatAge(ago(0), now)).toBe("<1m");
    expect(formatAge(ago(5), now)).toBe("5m");
    expect(formatAge(ago(125), now)).toBe("2h 5m");
    expect(formatAge(ago(60 * 50), now)).toBe("2d");
  });

  it("flags a card slower than its size limit in an active column only", () => {
    expect(isAged(cv({ id: "a", column: "in_progress", size: "S", since: ago(11) }), now)).toBe(true);
    expect(isAged(cv({ id: "a", column: "in_progress", size: "M", since: ago(11) }), now)).toBe(false);
    expect(isAged(cv({ id: "a", column: "todo", size: "S", since: ago(60) }), now)).toBe(false);
  });

  it("reads what a blocked card waits on from the thread, else from open dependencies", () => {
    const withThread = cv({
      id: "T1",
      column: "blocked",
      thread: [{ task_id: "T1", kind: "blocked_on", author: "dev", text: "x", iteration: 0, data: { role: "ops", unblock_card: "T9" } }],
    });
    expect(blockedOn(withThread, {})).toEqual({ role: "ops", card: "T9" });
    const dep = cv({ id: "T2", owner: "ops" });
    const byDeps = cv({ id: "T3", column: "blocked", dependsOn: ["T2"] });
    expect(blockedOn(byDeps, { T2: dep })).toEqual({ role: "ops", card: "T2" });
    expect(blockedOn(cv({ id: "T4" }), {})).toBeNull();
  });
});

describe("acceptanceState", () => {
  const result = (r: Record<string, unknown>): TypedSignal =>
    ({ id: "x", type: "AGENT_RESULT", summary: "", detail: null, from_agent: "dev", priority: "normal",
      iteration: 0, timestamp: "", data: { result: r } }) as TypedSignal;

  it("is unchecked without verdicts", () => {
    const items = acceptanceState(cv({ id: "T1", acceptance: ["a", "b"] }), []);
    expect(items.map((i) => i.met)).toEqual([false, false]);
  });

  it("reads met state from AGENT_RESULT acceptance and review items, latest wins", () => {
    const c = cv({ id: "T1", acceptance: ["a", "b"] });
    const items = acceptanceState(c, [
      result({ task_id: "T1", acceptance: [{ item: "a", met: true, evidence: "tests pass" }, { item: "b", met: true, evidence: "ok ok ok" }] }),
      result({ task_id: "T1", verdict: "fail", items: [{ item: "b", met: false, detail: "missing" }] }),
      result({ task_id: "T2", acceptance: [{ item: "b", met: true }] }),
    ]);
    expect(items.map((i) => i.met)).toEqual([true, false]);
    expect(items[0]?.evidence).toBe("tests pass");
  });

  it("falls back to thread notes and review issues; done cards are fully met", () => {
    const c = cv({
      id: "T1", acceptance: ["a", "b"],
      thread: [
        { task_id: "T1", kind: "note", author: "dev", text: "✓ a — it works", iteration: 0, data: {} },
        { task_id: "T1", kind: "note", author: "dev", text: "✓ b — it works", iteration: 0, data: {} },
        { task_id: "T1", kind: "review_issue", author: "rev", text: "b: broken", iteration: 0, data: { item: "b" } },
      ],
    });
    expect(acceptanceState(c, []).map((i) => i.met)).toEqual([true, false]);
    expect(acceptanceState({ ...c, column: "done" }, []).map((i) => i.met)).toEqual([true, true]);
  });

  it("mergeRanges lists merge data from the thread", () => {
    const c = cv({
      id: "T1",
      thread: [
        { task_id: "T1", kind: "note", author: "dev", text: "merged", iteration: 0, data: { merge: ["aaaa1111", "bbbb2222"] } },
        { task_id: "T1", kind: "note", author: "dev", text: "plain", iteration: 0, data: {} },
      ],
    });
    expect(mergeRanges(c)).toEqual([["aaaa1111", "bbbb2222"]]);
  });
});
