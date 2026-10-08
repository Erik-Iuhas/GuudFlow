import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { changeLabel, itemPhaseLabel } from "../model";
import { DesignTab } from "../DesignTab";
import { PhaseStrip } from "../PhaseStrip";
import { Ledger } from "../../work/Ledger";
import type { LedgerEntryWire } from "../../work/api";

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function json(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
}

afterEach(() => vi.restoreAllMocks());

const PHASES = [
  { id: "discover", title: "Discover", owner: "product_manager", gate: "g", state: "done", note: "" },
  { id: "design", title: "Design", owner: "tech_lead", gate: "the Lead seat approves", state: "current",
    note: "the first session recovers the design from the code" },
  { id: "build", title: "Build", owner: "tech_lead", gate: "g", state: "todo", note: "" },
] as const;

describe("design model", () => {
  it("labels design changes and item phases", () => {
    expect(changeLabel({ op: "add_component", name: "Dash", paths: ["web/dash/**"] }))
      .toBe("New component Dash owning web/dash/**");
    expect(changeLabel({ op: "add_interface", signature: "startDash(s) -> boolean" }))
      .toBe("New interface startDash(s) -> boolean");
    expect(changeLabel({ op: "resolve_debt", id: "DB1", how: "one owner now" })).toBe("Resolve debt DB1: one owner now");
    expect(itemPhaseLabel("design", [...PHASES])).toBe("waits for design");
    expect(itemPhaseLabel("ready", [...PHASES])).toBeNull();
    expect(itemPhaseLabel("accept", [...PHASES])).toBe("waiting for your review");
  });
});

describe("PhaseStrip", () => {
  it("shows the kind, every phase and the current one's note", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({
      kind: "game", kind_title: "A game", current: "design", phases: PHASES, items: {},
    }));
    wrap(<PhaseStrip projectId="p1" />);
    expect(await screen.findByText("A game")).toBeTruthy();
    expect(screen.getByText(/recovers the design/)).toBeTruthy();
    const states = screen.getAllByRole("listitem").map((li) => li.getAttribute("data-state"));
    expect(states).toEqual(["done", "current", "todo"]);
  });
});

const DESIGN = {
  design: {
    empty: false,
    components: [
      { id: "C1", name: "Game logic", paths: ["web/src/logic/**"], responsibility: "Rules of play.", status: "active" },
      { id: "C2", name: "Old", paths: ["x/**"], responsibility: "Gone.", status: "dropped" },
    ],
    interfaces: [{ id: "I1", name: "step", provider: "C1", kind: "function", signature: "step(s, i) -> s",
      users: [], status: "active" }],
    datatypes: [], flows: [{ id: "F1", title: "One step", kind: "sequence", diagram: "sequenceDiagram\n A->>B: step", status: "active" }],
    decisions: [], debt: [{ id: "DB1", text: "Two dash owners in abilities", status: "open" }],
  },
  drafts: [{
    id: "design-2", status: "open", by: "tech_lead", run_id: "run_4",
    turn: {
      groups: [{ name: "Dash owner", summary: "One dash component.", because: "two abilities started their own" },
        { name: "Flows", summary: "Draw the dash.", because: "readers need it" }],
      changes: [{ op: "add_component", group: "Dash owner", name: "Dash", paths: ["web/dash/**"] },
        { op: "add_flow", group: "Flows", title: "Dash" }],
    },
  }],
  markdown: "# Design",
};

describe("DesignTab", () => {
  it("shows the design and approves a draft without a reason left out", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      calls.push([url, init]);
      if (url.endsWith("/design")) return json(DESIGN);
      if (url.endsWith("/knowledge")) return json({ facts: null, notes: [
        { id: "K1", text: "Ghost tests set the mode first.", topic: "Testing", paths: [], status: "proposed", by: "lead" }] });
      return json({});
    });
    wrap(<DesignTab projectId="p1" />);
    expect(await screen.findByText("Game logic")).toBeTruthy();
    expect(screen.queryByText("Old")).toBeNull();                       // dropped components are hidden
    expect(screen.getByText("step(s, i) -> s")).toBeTruthy();
    expect(screen.getByText(/Two dash owners/)).toBeTruthy();
    expect(screen.getByText("New component Dash owning web/dash/**")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Include Flows"));
    fireEvent.click(screen.getByRole("button", { name: "Approve 1 of 2" }));
    await waitFor(() => expect(calls.some(([u]) => u.endsWith("/design/drafts/design-2/approve"))).toBe(true));
    const approve = calls.find(([u]) => u.endsWith("/approve"));
    expect(JSON.parse(String(approve?.[1]?.body))).toEqual({ reject_groups: ["Flows"] });
    expect(await screen.findByText("Ghost tests set the mode first.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some(([u]) => u.endsWith("/knowledge/K1/approve"))).toBe(true));
  });
});

describe("Ledger", () => {
  it("lets a person confirm only what no test settled", () => {
    const entries: LedgerEntryWire[] = [
      { check: "R1.1", state: "verified", method: "unit", tests: ["dash > R1.1 spends a charge"], detail: "" },
      { check: "R1.2", state: "claimed", method: "visual", tests: [], detail: "the reviewer said so" },
      { check: "R1.3", state: "failing", method: "unit", tests: ["x"], detail: "" },
    ];
    const toggle = vi.fn();
    render(<Ledger entries={entries} confirmed={[]} onToggle={toggle} />);
    expect(screen.getByText(/1 test failing, 1 claimed by the team, 1 verified by a test/)).toBeTruthy();
    expect(screen.queryByLabelText("I checked R1.3 myself")).toBeNull();
    expect(screen.queryByLabelText("I checked R1.1 myself")).toBeNull();
    fireEvent.click(screen.getByLabelText("I checked R1.2 myself"));
    expect(toggle).toHaveBeenCalledWith("R1.2");
  });
});
