import { describe, expect, it } from "vitest";
import type { CardView } from "../../../stores/runEvents";
import { checkState } from "../CardDrawer";

const card = (over: Partial<CardView>): CardView => ({
  id: "T1", title: "t", owner: "dev", column: "in_progress", kind: "code", size: "S", priority: 0,
  round: 0, acceptance: [], thread: [], dependsOn: [], ...over,
});
const note = (kind: string, text: string) =>
  ({ task_id: "T1", kind, author: "x", text, iteration: 1, data: {} }) as CardView["thread"][number];

describe("checkState", () => {
  it("follows the card's thread and column", () => {
    expect(checkState(card({}), "R1.1")).toBe("open");
    expect(checkState(card({ thread: [note("note", "✓ R1.1: score resets — test passed")] }), "R1.1")).toBe("claimed");
    expect(checkState(card({ thread: [note("note", "✓ R1.1: x — y"), note("review_issue", "R1.1: score resets: not reset on level 2")] }), "R1.1")).toBe("unmet");
    expect(checkState(card({ column: "done" }), "R1.1")).toBe("met");
  });
});
