import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { RequirementWire, SpecWire } from "../api";
import { SpecPageView, originEvents, progressMark } from "../SpecPage";
import { itemEvents, recentUpdate } from "../../work/model";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const wrap = (ui: ReactNode) => render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);

const why = (name: string, quote: string | null) => ({ name, summary: `${name} summary.`, because: `${name} because.`, source: "person" as const, quote });

const dash: RequirementWire = {
  id: "R4", title: "Dash", text: "Every run starts with a dash.", kind: "feature", priority: "must",
  assumed: false, experiment: false, status: "active", source: "pm", version: 3,
  params: [{ key: "charges", value: 2 }, { key: "recharge", value: 6, unit: "s", assumed: true }],
  checks: [
    { id: "R4.1", text: "dash moves 3 tiles", state: "met", run_id: "r3", method: "unit", test: "dash.test.js" },
    { id: "R4.2", text: "a 3rd dash in 6 s is refused", state: "unknown", run_id: null },
  ],
  origin: { draft: "draft-2", at: 1, why: why("Abilities", "two abilities to pick from") },
  history: [
    { draft: "draft-4", at: 3, version: 1, why: why("Default dash", "Default dash with charges") },
    { draft: "draft-4", at: 3, version: 2, why: why("Default dash", "Default dash with charges") },
    { at: 4, by: "session:run_9", check_states: { "R4.1": "met", "R4.2": "unmet" } },
  ],
};
const old: RequirementWire = { ...dash, id: "R1", title: "No build step", params: [], origin: null, history: [],
  checks: [{ id: "R1.1", text: "opens from index.html", state: "met", run_id: "r1" }] };
const spec: SpecWire = { version: 5, requirements: [old, dash], decisions: [], rules: [] };

describe("requirement page", () => {
  it("marks progress and reads where a requirement came from", () => {
    expect(progressMark(old).mark).toBe("●");
    expect(progressMark(dash).mark).toBe("◐");
    expect(originEvents(dash).map((e) => [e.where, e.what, e.quote])).toEqual([
      ["draft-2", "created", "two abilities to pick from"],
      ["draft-4", "changed", "Default dash with charges"],
      ["run_9", "checked in a session", null],
    ]);
    expect(originEvents(dash)[2].summary).toBe("1 check met, 1 not met");
  });

  it("shows values, checks, origin and the work that serves it", () => {
    const onEdit = vi.fn();
    wrap(<SpecPageView spec={spec} onEdit={onEdit}
      items={[{ id: "B6", title: "Abilities", serves: ["R4"], status: "open", history: [{ run_id: "r3", outcome: "partial" }] } as never]} />);
    expect(screen.getByTestId("req-detail")).toHaveTextContent("No build step");      // first in the list
    fireEvent.click(screen.getByTestId("req-R4"));
    const detail = screen.getByTestId("req-detail");
    expect(detail).toHaveTextContent("Every run starts with a dash.");
    expect(detail).toHaveTextContent("dash.test.js");
    expect(within(detail).getByTestId("origin")).toHaveTextContent("Default dash with charges");
    expect(screen.getByTestId("work-R4")).toHaveTextContent("B6Abilitiesopen · sessions r3");
    fireEvent.click(within(detail).getByRole("button", { name: "Confirm" }));
    expect(onEdit).toHaveBeenCalledWith({ op: "set_param", id: "R4", key: "recharge", value: 6 });
    fireEvent.click(screen.getByRole("button", { name: /^Assumed/ }));
    expect(screen.getAllByTestId(/^req-R/).map((e) => e.dataset.testid)).toEqual(["req-R4"]);
  });

  it("confirms a requirement assumed as a whole", () => {
    const onEdit = vi.fn();
    wrap(<SpecPageView spec={{ ...spec, requirements: [{ ...old, assumed: true }] }} onEdit={onEdit} />);
    fireEvent.click(within(screen.getByTestId("assumed-req")).getByRole("button", { name: "Confirm" }));
    expect(onEdit).toHaveBeenCalledWith({ op: "edit_requirement", id: "R1", assumed: false });
  });
});

describe("item history", () => {
  const history = [
    { draft: "draft-2", at: 1, change: "created", why: why("Abilities", null) },
    { run_id: "r3", outcome: "partial", summary: "Dash works; Dark not started.", decision: "accepted" },
    { draft: "draft-4", at: 5, change: "edited: acceptance, title", why: why("Default dash", "Default dash with charges") },
  ];

  it("lists drafts and runs newest first and finds the update since the last run", () => {
    expect(itemEvents(history).map((e) => [e.where, e.what])).toEqual([
      ["draft-4", "edited: acceptance, title"], ["r3", "accepted"], ["draft-2", "created"],
    ]);
    expect(recentUpdate(history)?.quote).toBe("Default dash with charges");
    expect(recentUpdate(history.slice(0, 2))).toBeNull();
  });
});
