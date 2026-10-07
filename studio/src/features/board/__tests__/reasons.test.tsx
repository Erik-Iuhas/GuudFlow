import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { reasonGroups, splitCheckLine } from "../boardModel";
import { card, json, mockBackend, renderBoard } from "./helpers";

beforeEach(() => {
  localStorage.setItem("guudflow.token", "tok");
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const spec = {
  version: 3,
  requirements: [{
    id: "R5", title: "Burst walls", text: "Cracked walls break with a charge.", kind: "feature", priority: "must",
    assumed: false, experiment: false, status: "active", source: "pm", version: 1,
    checks: [{ id: "R5.2", text: "a charge bursts the wall you face", state: "unknown", run_id: null }],
  }],
  decisions: [],
  rules: [],
};

const cards = [
  card({ id: "T1", title: "Burst on charge", serves: ["R5.2"], backlog_id: "B5", acceptance: ["R5.2: a charge bursts the wall you face"] }),
  card({ id: "T2", title: "Shared grid helper", supports: ["T1"], column: "done" }),
  card({ id: "T3", title: "Tidy logs" }),
];

function backend() {
  return mockBackend(cards, (c) => {
    if (c.url.endsWith("/api/runs/r1")) return json({ run_id: "r1", project_id: "p1", status: "running" });
    if (c.url.endsWith("/api/projects/p1/spec")) return json(spec);
    return undefined;
  });
}

describe("cards link to the spec", () => {
  it("groups cards by requirement, then support work, then unlinked", () => {
    const groups = reasonGroups(cards.map((w) => ({ id: w.id, title: w.title, owner: w.owner, column: w.column, kind: "", size: "",
      priority: 0, round: 0, acceptance: [], thread: [], dependsOn: [], serves: w.serves, supports: w.supports })), { R5: "Burst walls" });
    expect(groups.map((g) => [g.key, g.title, g.cards.map((c) => c.id), g.checks])).toEqual([
      ["R5", "Burst walls", ["T1"], ["R5.2"]],
      ["support", "Support work", ["T2"], []],
      ["none", "Not linked to the spec", ["T3"], []],
    ]);
    expect(splitCheckLine("r5.2: bursts")).toEqual({ check: "R5.2", rest: "bursts" });
    expect(splitCheckLine("plain")).toEqual({ check: null, rest: "plain" });
  });

  it("shows chips on tiles, a by-requirement view and the reason in the drawer", async () => {
    backend();
    renderBoard();
    expect(await screen.findByTestId("links-T1")).toHaveTextContent("R5.2");
    expect(screen.getByTestId("links-T2")).toHaveTextContent("for T1");
    expect(screen.queryByTestId("links-T3")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "By requirement" }));
    const view = screen.getByTestId("by-requirement");
    const r5 = await within(view).findByRole("region", { name: "R5 Burst walls" });
    expect(r5).toHaveTextContent("0 of 1 card done");
    expect(within(view).getByRole("region", { name: "Support work" })).toHaveTextContent("T2");

    fireEvent.click(within(r5).getByRole("button", { name: /T1/ }));
    const why = await screen.findByRole("region", { name: "Why this card" });
    expect(within(why).getByTestId("why-R5")).toHaveTextContent("R5Burst walls→B5→T1");
    expect(why).toHaveTextContent("a charge bursts the wall you face");
  });
});
