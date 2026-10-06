import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { BacklogItemWire, RunBranchWire } from "../api";
import { BacklogList } from "../BacklogList";
import { ReviewList } from "../ReviewList";
import { changeSummary, groupBacklog, movedPriority, parseAcceptance, parseItemIds, splitRuns, topOpen } from "../model";

function item(id: string, over: Partial<BacklogItemWire> = {}): BacklogItemWire {
  return {
    id, title: `Item ${id}`, acceptance: [], kind: "code", size: "M", priority: 0, status: "open",
    notes: "", files_hint: [], source: "human", created_at: 0, updated_at: 0, run_id: null,
    pending: null, history: [], depends_on: [], ...over,
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
    fireEvent.click(screen.getByRole("button", { name: "Start session with 1 item" }));
    expect(props.onStart).toHaveBeenCalled();
  });

  it("shows items held by a run with their pending outcome and the last run's note", () => {
    render(<BacklogList {...base} selected={[]} items={[
      item("B1", { status: "in_run", run_id: "run_x", pending: "done" }),
      item("B2", { history: [{ run_id: "run_w", summary: "half the endpoints" }] }),
    ]} />);
    expect(within(screen.getByTestId("backlog-B1")).getByText("finished, waiting for your review")).toBeInTheDocument();
    expect(screen.getByText("Last run (run_w): half the endpoints")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start session with 0 items" })).toBeDisabled();
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

describe("plan 14 additions", () => {
  it("shows a failed check with its output and offers a reconcile run on conflict", () => {
    const onContinue = vi.fn();
    render(<MemoryRouter><ReviewList pending={[run("run_a", {
      check: { command: "pytest -q", status: "failed", exit_code: 1, output: "1 failed", duration_s: 3,
        where: "docker", reason: "" } })]} decided={[run("run_0", { state: "superseded" })]} changes={{}}
      onViewChanges={vi.fn()} onDecide={vi.fn()} onContinue={onContinue}
      error={{ runId: "run_a", message: "main changed", files: ["same.py"] }} /></MemoryRouter>);
    expect(screen.getByText("check failed")).toBeInTheDocument();
    expect(screen.getByText("`pytest -q` exited with 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reconcile in a new run" }));
    expect(onContinue).toHaveBeenCalled();
    expect(screen.getByText("continued by a later run")).toBeInTheDocument();
  });

  it("labels dependencies and spend on items, and parses 'after' ids", () => {
    render(<BacklogList onToggle={vi.fn()} onMove={vi.fn()} onDrop={vi.fn()} onReopen={vi.fn()}
      onAdd={vi.fn()} onStart={vi.fn()} selected={[]}
      items={[item("B2", { depends_on: ["B1"], total_cost_usd: 1.5 })]} />);
    expect(screen.getByText("after B1")).toBeInTheDocument();
    expect(screen.getByText("$1.50 spent")).toBeInTheDocument();
    expect(parseItemIds("b2, B5 x B2")).toEqual(["B2", "B5"]);
  });

  it("header: remote, sync result and autopilot budget", async () => {
    const { WorkHeader, Suggestions } = await import("../WorkHeader");
    const onSync = vi.fn();
    render(<WorkHeader remote={{ url: "https://github.com/acme/app.git", branch: "main", publish: "pr", token: false }}
      autopilot={{ enabled: true, next_due: 1_800_000_000, week_spend_usd: 4, max_usd_per_week: 20 }}
      lastSync={{ status: "conflict", main: "x", detail: "README.md", at: 1 }} onSync={onSync} />);
    fireEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(onSync).toHaveBeenCalled();
    expect(screen.getByText("no token set")).toBeInTheDocument();
    expect(screen.getByText("not synced: conflict: README.md")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-status")).toHaveTextContent("$4.00 of $20.00 this week");
    const onAdd = vi.fn();
    render(<Suggestions proposals={[{ title: "Audit log", acceptance: ["logged"], kind: "code", size: "M", why: "" }]}
      onAdd={onAdd} onDismiss={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ title: "Audit log" }));
  });

  it("settings: remote clears when the url is emptied; auto-accept warns without a check", async () => {
    const { WorkSettings } = await import("../WorkSettings");
    const onChange = vi.fn();
    const { rerender } = render(<WorkSettings gates={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "/srv/git/app.git" } });
    expect(onChange).toHaveBeenLastCalledWith({ project_remote: { url: "/srv/git/app.git", branch: "main", publish: "none" } });
    rerender(<WorkSettings gates={{ project_remote: { url: "/srv/git/app.git" } }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith({ project_remote: null });
    rerender(<WorkSettings gates={{ autopilot: { enabled: true, every_hours: 6, take: 2, max_usd_per_run: 3,
      max_usd_per_week: 10, auto_accept: true } }} onChange={onChange} />);
    expect(screen.getByText(/Auto-accept needs a check before review/)).toBeInTheDocument();
  });
});
