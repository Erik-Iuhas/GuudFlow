import { describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { createRunStore, type RunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import type { RawSignal, SignalType } from "../../../lib/runEvents.gen";
import { OverseerPanel } from "../OverseerPanel";
import { buildWakes } from "../model";

let n = 0;
function sig(type: SignalType, from: string | null, detail: unknown = null, summary = type.toLowerCase()): RawSignal {
  n += 1;
  return {
    id: `o${n}`, type, summary, from_agent: from, priority: "normal", iteration: 1,
    detail: detail === null ? null : JSON.stringify(detail),
    timestamp: `2026-03-01T10:00:${String(n % 60).padStart(2, "0")}+00:00`,
  };
}

const wake = (stalls: string[], assessment: string): RawSignal =>
  sig("AGENT_RESULT", "overseer", { result: { assessment }, stalls }, `overseer: ${assessment}`);
const iv = (d: Record<string, unknown>): RawSignal => sig("OVERSEER_INTERVENTION", "overseer", d);

function fixtureStore(): RunStore {
  const store = createRunStore("r1");
  store.getState().ingest([
    wake(["same_review_issue dev: lint failed twice", "no_progress reviewer: 12 min idle"], "dev is looping on lint"),
    iv({ type: "rebrief", role: "dev", task_id: "T1", rationale: "Tell dev to run the linter first", needs_human: false, addendum: "Run npm run lint before submitting" }),
    iv({ type: "extend_review", author_role: "dev", reviewer_role: "reviewer", rationale: "One more round is cheaper than a split", needs_human: false }),
    wake(["burn_without_output dev: $2 spent"], "dev burned budget"),
    iv({ type: "propose_team_change", rationale: "Add a tester role", needs_human: true, summary: "Add tester" }),
  ]);
  return store;
}

function wrap(store: RunStore) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <RunStoreProvider store={store}><OverseerPanel /></RunStoreProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("buildWakes", () => {
  it("groups interventions under the overseer result before them and carries stalls", () => {
    const store = fixtureStore();
    const wakes = buildWakes(store.getState().signals);
    expect(wakes).toHaveLength(2);
    expect(wakes[0]?.stalls).toHaveLength(2);
    expect(wakes[0]?.assessment).toBe("dev is looping on lint");
    expect(wakes[0]?.interventions.map((i) => i.data.type)).toEqual(["rebrief", "extend_review"]);
    expect(wakes[1]?.interventions.map((i) => i.data.type)).toEqual(["propose_team_change"]);
  });

  it("gives an intervention with no preceding result a wake of its own", () => {
    const store = createRunStore("r2");
    store.getState().ingest([iv({ type: "pause", rationale: "wake cap reached", needs_human: true })]);
    const wakes = buildWakes(store.getState().signals);
    expect(wakes).toHaveLength(1);
    expect(wakes[0]?.stalls).toEqual([]);
  });

  it("attaches outcomes by signal id", () => {
    const store = fixtureStore();
    const first = store.getState().signals.find((s) => s.type === "OVERSEER_INTERVENTION");
    const wakes = buildWakes(store.getState().signals, new Map([[first?.id ?? "", true]]));
    expect(wakes[0]?.interventions[0]?.worked).toBe(true);
    expect(wakes[0]?.interventions[1]?.worked).toBeNull();
  });
});

describe("OverseerPanel", () => {
  it("shows wakes newest first with detectors, assessment and interventions", () => {
    wrap(fixtureStore());
    const wakes = screen.getAllByTestId("wake");
    expect(wakes).toHaveLength(2);
    expect(wakes[0]).toHaveTextContent("Wake 2");
    expect(wakes[0]).toHaveTextContent("burn_without_output dev");
    const first = wakes[1] as HTMLElement;
    expect(within(first).getAllByTestId("stall")).toHaveLength(2);
    expect(first).toHaveTextContent("dev is looping on lint");
    const items = within(first).getAllByTestId("intervention");
    expect(items[0]).toHaveTextContent("rebrief");
    expect(items[0]).toHaveTextContent("Tell dev to run the linter first");
    expect(items[0]).toHaveTextContent("Run npm run lint before submitting");
    expect(items[1]).toHaveTextContent("extend review");
    expect(items[1]).toHaveTextContent("reviewer reviews dev");
    const team = within(wakes[0] as HTMLElement).getByTestId("intervention");
    expect(team).toHaveTextContent("needs human");
  });

  it("has an empty state", () => {
    wrap(createRunStore("r3"));
    expect(screen.getByText(/No wakes/)).toBeInTheDocument();
  });

  it("links a pending team-change approval to the approvals bar", () => {
    const store = createRunStore("r4");
    store.getState().ingest([
      sig("APPROVAL_NEEDED", "overseer", null, "Add a tester"),
    ]);
    // APPROVAL_NEEDED detail is the plain "approval_id=...; kind=..." text.
    const raw = { ...sig("APPROVAL_NEEDED", "overseer", null, "Add a tester"), detail: "approval_id=team_1; kind=team_change; context={}" };
    act(() => store.getState().ingest(raw));
    const bar = document.createElement("div");
    bar.setAttribute("data-approvals", "");
    const btn = document.createElement("button");
    bar.appendChild(btn);
    document.body.appendChild(bar);
    wrap(store);
    const focus = vi.spyOn(btn, "focus");
    screen.getByRole("button", { name: /Go to approvals bar/ }).click();
    expect(focus).toHaveBeenCalled();
    bar.remove();
  });
});
