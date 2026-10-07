import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Markdown } from "../../../lib/markdown";
import type { DraftChangeWire, DraftWire, PlanMessageWire, SpecWire } from "../api";
import { DraftPanel } from "../DraftPanel";
import { foldThread, wordDiff } from "../model";
import { SpecView, assumptions, parseValue } from "../SpecView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

const spec: SpecWire = {
  version: 4,
  requirements: [
    {
      id: "R1", title: "Default dash", text: "Every run starts with a dash.", kind: "feature", priority: "must",
      assumed: false, experiment: false, status: "active", source: "pm", version: 1,
      params: [
        { key: "charges", value: 2, assumed: true },
        { key: "recharge", value: 10, unit: "s", assumed: false },
      ],
      checks: [
        { id: "R1.1", text: "a new run has 2 charges", state: "met", run_id: "r1", method: "unit", test: "tests/dash.test.js::charges" },
        { id: "R1.2", text: "dashing through a ghost eats it", state: "unknown", run_id: null, method: "manual" },
      ],
    },
    {
      id: "R2", title: "Rush family", text: "Rush levels have extra power pellets.", kind: "feature", priority: "should",
      assumed: true, experiment: false, status: "active", source: "pm", version: 1,
      checks: [{ id: "R2.1", text: "8 power pellets", state: "met", run_id: "r1" }],
    },
  ],
  decisions: [],
  rules: [],
};

const dc = (cid: string, change: DraftChangeWire["change"]): DraftChangeWire => ({ cid, change, accepted: null, by: "pm", touched: false });
const draftOf = (changes: DraftChangeWire[]): DraftWire => ({ id: "draft-5", base_version: 4, status: "open", changes });

describe("markdown", () => {
  it("renders bold, code and lists as elements, never as raw HTML", () => {
    render(<Markdown text={"**Default dash** uses `charges`.\n\n- one\n- two <b>x</b>\n  - nested"} />);
    expect(screen.getByText("Default dash").tagName).toBe("STRONG");
    expect(screen.getByText("charges").tagName).toBe("CODE");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText(/two <b>x<\/b>/)).toBeTruthy(); // the tag stays text
    expect(document.querySelector("b")).toBeNull();
  });
});

describe("model", () => {
  it("diffs words", () => {
    expect(wordDiff("has 2 charges", "has 3 charges")).toEqual([
      { text: "has ", kind: "same" }, { text: "2", kind: "del" }, { text: "3", kind: "add" }, { text: " charges", kind: "same" },
    ]);
  });

  it("folds earlier drafts' threads and keeps the open draft's thread", () => {
    const m = (id: number, draft: string | null): PlanMessageWire => ({ id, at: 1, author: "pm", text: `m${id}`, draft_id: draft, mode: "update" });
    const { folded, open } = foldThread([m(1, "draft-1"), m(2, null), m(3, "draft-2"), m(4, null), m(5, "draft-3")], "draft-3");
    expect(folded.map((g) => [g.label, g.messages.map((x) => x.id)])).toEqual([["Draft 1", [1, 2]], ["Draft 2", [3]]]);
    expect(open.map((x) => x.id)).toEqual([4, 5]);
  });

  it("lists assumptions and parses typed values", () => {
    expect(assumptions(spec).map((a) => [a.reqId, a.key, a.raw])).toEqual([["R1", "charges", 2], ["R2", null, null]]);
    expect([parseValue("3"), parseValue("true"), parseValue("fast")]).toEqual([3, true, "fast"]);
  });
});

describe("DraftPanel", () => {
  const draft = draftOf([
    dc("c1", { op: "set_param", id: "R1", key: "charges", value: 3, group: "Default dash" }),
    dc("c2", { op: "edit_check", id: "R1.1", text: "a new run has 3 charges", group: "Default dash" }),
    dc("c3", { op: "set_statement", id: "R2", text: "Rush levels have eight power pellets.", group: "Families" }),
    dc("c4", { op: "drop_item", id: "B9", reason: "covered by tests" }),
  ]);

  it("shows structured diffs grouped by theme", () => {
    wrap(<DraftPanel draft={draft} spec={spec} onTick={() => {}} onApprove={() => {}} onDiscard={() => {}} />);
    const themes = screen.getAllByTestId("theme");
    expect(themes.map((t) => within(t).getByRole("heading", { level: 3 }).textContent)).toEqual(["Default dash", "Families", "Other changes"]);
    const param = within(screen.getByTestId("change-c1")).getByTestId("param-table");
    expect(param.querySelector("del")?.textContent).toBe("2");
    expect(param).toHaveTextContent("3");
    expect(screen.getByTestId("change-c1")).toHaveTextContent("R1 · Default dash");
    const check = within(screen.getByTestId("change-c2")).getByTestId("check-row");
    expect(check.querySelector("del")?.textContent).toBe("2");
    expect(check.querySelector("ins")?.textContent).toBe("3");
    expect(check).toHaveTextContent("tests/dash.test.js::charges");
    const statement = within(screen.getByTestId("change-c3")).getByTestId("text-diff");
    expect(statement.querySelector("ins")?.textContent).toBe("eight");
  });

  it("ticks a whole theme and moves with the keyboard", () => {
    const onTick = vi.fn();
    wrap(<DraftPanel draft={draft} spec={spec} onTick={onTick} onApprove={() => {}} onDiscard={() => {}} />);
    fireEvent.click(within(screen.getAllByTestId("theme")[0]).getByRole("button", { name: "Accept all" }));
    expect(onTick.mock.calls).toEqual([["c1", true], ["c2", true]]);
    onTick.mockClear();
    const panel = screen.getByLabelText(/j and k move/);
    fireEvent.keyDown(panel, { key: "j" });
    fireEvent.keyDown(panel, { key: "n" });
    expect(onTick).toHaveBeenCalledWith("c2", false);
  });
});

describe("SpecView", () => {
  it("shows params, check methods, tests and the work that serves a requirement", () => {
    wrap(<SpecView spec={spec} items={[{ id: "B3", title: "Build the dash", serves: ["R1"], status: "open" } as never]} />);
    const r1 = screen.getByTestId("req-R1");
    expect(r1).toHaveTextContent("Default dash");
    expect(r1).toHaveTextContent("tests/dash.test.js::charges");
    expect(within(r1).getAllByText(/^test$|^manual$/)).toHaveLength(2);
    expect(screen.getByTestId("work-R1")).toHaveTextContent("B3 Build the dash (open)");
  });

  it("filters and confirms or edits assumptions", () => {
    const onEdit = vi.fn();
    wrap(<SpecView spec={spec} onEdit={onEdit} />);
    fireEvent.click(screen.getByRole("button", { name: "Assumed" }));
    expect(screen.getAllByTestId(/^req-/).map((e) => e.dataset.testid)).toEqual(["req-R1", "req-R2"]);
    fireEvent.click(screen.getByRole("button", { name: "Not met yet" }));
    expect(screen.getAllByTestId(/^req-/).map((e) => e.dataset.testid)).toEqual(["req-R1"]);
    const charges = screen.getByTestId("assumption-R1-charges");
    fireEvent.click(within(charges).getByRole("button", { name: "Confirm" }));
    expect(onEdit).toHaveBeenLastCalledWith({ op: "set_param", id: "R1", key: "charges", value: 2 });
    fireEvent.click(within(charges).getByRole("button", { name: "Edit" }));
    fireEvent.change(within(charges).getByLabelText("New value for charges"), { target: { value: "3" } });
    fireEvent.click(within(charges).getByRole("button", { name: "Save" }));
    expect(onEdit).toHaveBeenLastCalledWith({ op: "set_param", id: "R1", key: "charges", value: 3 });
    fireEvent.click(within(screen.getByTestId("assumption-R2-req")).getByRole("button", { name: "Confirm" }));
    expect(onEdit).toHaveBeenLastCalledWith({ op: "edit_requirement", id: "R2", assumed: false });
  });
});
