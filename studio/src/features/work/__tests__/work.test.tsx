import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { BacklogItemWire, RunBranchWire } from "../api";
import { BacklogList } from "../BacklogList";
import { ReviewList } from "../ReviewList";
import { changeSummary, groupBacklog, movedPriority, parseAcceptance, splitRuns, topOpen } from "../model";

function item(id: string, over: Partial<BacklogItemWire> = {}): BacklogItemWire {
  return {
    id, title: `Item ${id}`, acceptance: [], kind: "code", size: "M", priority: 0, status: "open",
    notes: "", files_hint: [], source: "human", created_at: 0, updated_at: 0, run_id: null,
    pending: null, history: [], ...over,
  };
}

function run(id: string, over: Partial<RunBranchWire> = {}): RunBranchWire {
  return {
    run_id: id, state: "pending", base: "a", head: "b", commits: 2, files: ["app.py"],
    submitted_at: 1, decided_at: null, merged: "", note: "", parent_run: null, ...over,
  };
}

describe("work model", () => {
  it("groups by status and orders open items by priority then id", () => {
    const g = groupBacklog([item("B3", { priority: 1 }), item("B1"), item("B2", { priority: 1 }),
      item("B4", { status: "in_run" }), item("B5", { status: "done" })]);
    expect(g.open.map((i) => i.id)).toEqual(["B2", "B3", "B1"]);
    expect(g.inRun.map((i) => i.id)).toEqual(["B4"]);
    expect(g.done.map((i) => i.id)).toEqual(["B5"]);
  });

  it("moves an item past its neighbour and stops at the ends", () => {
    const open = [item("B1", { priority: 5 }), item("B2", { priority: 3 }), item("B3", { priority: 3 })];
    expect(movedPriority(open, "B2", "up")).toBe(6);
    expect(movedPriority(open, "B2", "down")).toBe(2);
    expect(movedPriority(open, "B1", "up")).toBeNull();
    expect(movedPriority(open, "B3", "down")).toBeNull();
  });

  it("parses acceptance lines and summarises changes", () => {
    expect(parseAcceptance("- [ ] works\n\n2. is fast\n* tested ")).toEqual(["works", "is fast", "tested"]);
    expect(changeSummary({ commits: 1, files: ["a"] })).toBe("1 commit, 1 file");
    expect(topOpen([item("B1"), item("B2"), item("B3"), item("B4")])).toEqual(["B1", "B2", "B3"]);
    expect(splitRuns([run("r1"), run("r2", { state: "accepted" })]).decided.map((r) => r.run_id)).toEqual(["r2"]);
  });
});

describe("BacklogList", () => {
  const base = {
    onToggle: vi.fn(), onMove: vi.fn(), onDrop: vi.fn(), onReopen: vi.fn(), onAdd: vi.fn(), onStart: vi.fn(),
  };

  it("lets the user pick items, reorder them and start a run", () => {
    const props = { ...base, onToggle: vi.fn(), onMove: vi.fn(), onStart: vi.fn() };
    render(<BacklogList {...props} selected={["B1"]}
      items={[item("B1", { priority: 2, acceptance: ["login works"] }), item("B2", { priority: 1 })]} />);
    expect(screen.getByText("login works")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Include B2 in the next run" }));
    expect(props.onToggle).toHaveBeenCalledWith("B2");
    fireEvent.click(screen.getByRole("button", { name: "Move B2 up" }));
    expect(props.onMove).toHaveBeenCalledWith("B2", 3);
    expect(screen.getByRole("button", { name: "Move B1 up" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Start a run with 1 item" }));
    expect(props.onStart).toHaveBeenCalled();
  });

  it("shows items held by a run with their pending outcome and the last run's note", () => {
    render(<BacklogList {...base} selected={[]} items={[
      item("B1", { status: "in_run", run_id: "run_x", pending: "done" }),
      item("B2", { history: [{ run_id: "run_w", summary: "half the endpoints" }] }),
    ]} />);
    expect(within(screen.getByTestId("backlog-B1")).getByText("finished, waiting for your review")).toBeInTheDocument();
    expect(screen.getByText("Last run (run_w): half the endpoints")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start a run with 0 items" })).toBeDisabled();
  });

  it("adds an item with one acceptance check per line", () => {
    const onAdd = vi.fn();
    render(<BacklogList {...base} onAdd={onAdd} selected={[]} items={[]} />);
    fireEvent.change(screen.getByLabelText("New item"), { target: { value: "Reset password" } });
    fireEvent.change(screen.getByLabelText("Done when (one check per line)"), { target: { value: "sends mail\n- expires" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to backlog" }));
    expect(onAdd).toHaveBeenCalledWith({ title: "Reset password", acceptance: ["sends mail", "expires"], kind: "code", size: "M" });
  });
});

describe("ReviewList", () => {
  it("accepts or rejects with a note, and shows a conflict's files", () => {
    const onDecide = vi.fn();
    const { rerender } = render(<MemoryRouter><ReviewList pending={[run("run_a")]} decided={[]} changes={{}}
      onViewChanges={vi.fn()} onDecide={onDecide} onContinue={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText("2 commits, 1 file")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Note for run_a"), { target: { value: "ship it" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept into the project" }));
    expect(onDecide).toHaveBeenCalledWith("run_a", true, "ship it");
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(onDecide).toHaveBeenLastCalledWith("run_a", false, "ship it");
    rerender(<MemoryRouter><ReviewList pending={[run("run_a")]} decided={[run("run_0", { state: "accepted", note: "fine" })]}
      changes={{ run_a: "+added\n-removed" }} onViewChanges={vi.fn()} onDecide={onDecide} onContinue={vi.fn()}
      error={{ runId: "run_a", message: "main changed", files: ["same.py"] }} /></MemoryRouter>);
    expect(screen.getByRole("alert")).toHaveTextContent("main changedsame.py");
    expect(screen.getByTestId("review-diff")).toHaveTextContent("+added");
    expect(screen.getByText("Earlier runs (1)")).toBeInTheDocument();
  });

  it("says so when nothing waits", () => {
    render(<MemoryRouter><ReviewList pending={[]} decided={[]} changes={{}} onViewChanges={vi.fn()}
      onDecide={vi.fn()} onContinue={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText(/No run is waiting for review/)).toBeInTheDocument();
  });
});
