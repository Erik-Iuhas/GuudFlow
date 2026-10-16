import { describe, expect, it } from "vitest";
import type { RawSignal, SignalType } from "../../lib/runEvents.gen";
import { createRunStore } from "../runEvents";

let counter = 0;
function sig(
  type: SignalType,
  opts: { from?: string | null; summary?: string; detail?: unknown; id?: string; ts?: string } = {},
): RawSignal {
  counter += 1;
  const detail =
    opts.detail === undefined ? null : typeof opts.detail === "string" ? opts.detail : JSON.stringify(opts.detail);
  return {
    id: opts.id ?? `s${counter}`,
    type,
    summary: opts.summary ?? type.toLowerCase(),
    detail,
    from_agent: opts.from === undefined ? null : opts.from,
    priority: "normal",
    iteration: 1,
    timestamp: opts.ts ?? `2026-01-01T00:00:${String(counter % 60).padStart(2, "0")}`,
  };
}

const state = (store: ReturnType<typeof createRunStore>) => store.getState();

describe("run event store: ingest", () => {
  it("starts empty and live", () => {
    const store = createRunStore("r1");
    expect(state(store).runId).toBe("r1");
    expect(state(store).live).toBe(true);
    expect(state(store).signals).toEqual([]);
  });

  it("ingests a snapshot in arrival order and indexes by id", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("STARTED", { from: "dev", id: "a" }),
      sig("COMPLETE", { from: "dev", id: "b" }),
    ]);
    expect(state(store).signals.map((s) => s.id)).toEqual(["a", "b"]);
    expect(state(store).byId.get("b")?.type).toBe("COMPLETE");
  });

  it("dedupes by id across batches (snapshot replay after reconnect)", () => {
    const store = createRunStore("r1");
    const started = sig("STARTED", { from: "dev", id: "a" });
    state(store).ingest([started]);
    state(store).ingest([started, sig("TOOL_CALL", { from: "dev", id: "b", detail: { tool: "bash", status: "ok", input: "ls", duration_ms: 3 } })]);
    state(store).ingest(started);
    expect(state(store).signals).toHaveLength(2);
    expect(state(store).agents.dev.spawns).toBe(1);
    expect(state(store).agents.dev.toolCalls).toBe(1);
  });

  it("returns the same state object when nothing new arrived", () => {
    const store = createRunStore("r1");
    const s = sig("STARTED", { from: "dev" });
    state(store).ingest(s);
    const before = state(store);
    state(store).ingest(s);
    expect(state(store)).toBe(before);
  });

  it("parses detail once for JSON types and leaves data null otherwise", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("TOOL_CALL", { from: "dev", detail: { tool: "bash", status: "ok", input: "ls", duration_ms: 1 } }),
      sig("STARTED", { from: "dev", detail: '{"not":"parsed"}' }),
    ]);
    const [tool, started] = state(store).signals;
    expect(tool.data).toMatchObject({ tool: "bash" });
    expect(started.data).toBeNull();
  });

  it("never throws on malformed detail and keeps the signal with data null", () => {
    const store = createRunStore("r1");
    expect(() =>
      state(store).ingest([
        sig("TASK_MOVED", { detail: "{not json" }),
        sig("AGENT_RESULT", { from: "dev", detail: "[1,2,3]" }),
        sig("WEB_REQUEST", { detail: "null" }),
        sig("ARTIFACT", { detail: "" }),
      ]),
    ).not.toThrow();
    expect(state(store).signals).toHaveLength(4);
    expect(state(store).signals.every((s) => s.data === null)).toBe(true);
    expect(state(store).cards).toEqual({});
    expect(state(store).artifacts).toEqual([]);
  });

  it("ignores structurally wrong detail without corrupting slices", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("TASK_MOVED", { detail: { to: "done" } }), // missing task_id
      sig("TASK_THREAD", { detail: { text: "x" } }),
      sig("ARTIFACT", { detail: { kind: "png" } }), // missing path
      sig("WEB_REQUEST", { detail: { query: "q" } }), // missing tool
    ]);
    expect(state(store).cards).toEqual({});
    expect(state(store).artifacts).toEqual([]);
    expect(state(store).web).toEqual([]);
  });

  it("skips entries without an id", () => {
    const store = createRunStore("r1");
    expect(() => state(store).ingest([{} as RawSignal])).not.toThrow();
    expect(state(store).signals).toHaveLength(0);
  });

  it("reset clears everything, including remembered decisions", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("STARTED", { from: "dev" }),
      sig("APPROVAL_GRANTED", { detail: "approval_id=p1; kind=x" }),
    ]);
    state(store).reset();
    expect(state(store).signals).toEqual([]);
    expect(state(store).agents).toEqual({});
    state(store).ingest(sig("APPROVAL_NEEDED", { from: "runner", detail: "approval_id=p1; kind=x" }));
    expect(state(store).approvals).toHaveLength(1);
  });

  it("setLive only changes when the value differs", () => {
    const store = createRunStore("r1");
    state(store).setLive(false);
    expect(state(store).live).toBe(false);
  });
});

describe("run event store: mapping table", () => {
  it("STARTED / COMPLETE / FAILED / BLOCKED drive agent state and spawns", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("STARTED", { from: "dev" }));
    expect(state(store).agents.dev).toMatchObject({ state: "running", spawns: 1 });
    state(store).ingest(sig("BLOCKED", { from: "dev", detail: { on: "qa" } }));
    expect(state(store).agents.dev.state).toBe("blocked");
    state(store).ingest(sig("STARTED", { from: "dev" }));
    expect(state(store).agents.dev.spawns).toBe(2);
    state(store).ingest(sig("COMPLETE", { from: "dev" }));
    expect(state(store).agents.dev.state).toBe("complete");
    state(store).ingest(sig("FAILED", { from: "qa" }));
    expect(state(store).agents.qa.state).toBe("failed");
  });

  it("ignores state signals with no agent", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("STARTED", { from: null }));
    expect(state(store).agents).toEqual({});
  });

  it("CAPABILITIES lands on the agent", () => {
    const store = createRunStore("r1");
    const caps = {
      role_type: "builder", requested: {}, effective: { web: "off" }, digest: "d", mcp_tools: [], model: "m", warnings: [],
    };
    state(store).ingest(sig("CAPABILITIES", { from: "dev", detail: caps }));
    expect(state(store).agents.dev.capabilities?.role_type).toBe("builder");
  });

  it("TOOL_CALL counts and feeds the lane", () => {
    const store = createRunStore("r1");
    const call = { tool: "bash", status: "ok", input: "ls", duration_ms: 5 };
    state(store).ingest([sig("TOOL_CALL", { from: "dev", detail: call }), sig("TOOL_CALL", { from: "dev", detail: call })]);
    expect(state(store).agents.dev.toolCalls).toBe(2);
    expect(state(store).toolFeed.dev).toHaveLength(2);
    expect(state(store).toolFeed.qa).toBeUndefined();
  });

  it("TEAM records each role's worker name", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("TEAM", { from: "runner", detail: { members: [
      { role: "builder", name: "Maya", role_type: "builder" },
      { role: "reviewer", name: "Ivy", role_type: "reviewer" },
      { role: "bad", name: "" },
    ] } }));
    expect(state(store).names).toEqual({ builder: "Maya", reviewer: "Ivy" });
  });

  it("AGENT_RESULT sets lastResult and keeps the role's running cost total", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("AGENT_RESULT", { from: "dev", summary: "first", detail: { result: {}, cost: 0.25 } }),
      sig("AGENT_RESULT", { from: "dev", summary: "second", detail: { result: {}, cost: 0.5 } }),
      // A late or replayed result never moves the total backwards.
      sig("AGENT_RESULT", { from: "dev", summary: "third", detail: { result: {}, cost: 0.4 } }),
      sig("AGENT_RESULT", { from: "qa", summary: "qa done", detail: { result: {} } }),
    ]);
    expect(state(store).agents.dev.lastResult).toBe("third");
    expect(state(store).agents.dev.cost).toBeCloseTo(0.5);
    expect(state(store).costs.dev).toBeCloseTo(0.5);
    expect(state(store).costs.qa).toBeUndefined();
    expect(state(store).agents.qa.lastResult).toBe("qa done");
  });

  it("TASK_ASSIGNED makes a stub card from the id and summary", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("TASK_ASSIGNED", { detail: "t1", summary: "task:t1 → dev: Build the login form" }));
    expect(state(store).cards.t1).toMatchObject({
      id: "t1", owner: "dev", title: "Build the login form", column: "todo", stub: true,
    });
  });

  it("TASK_MOVED updates column, round and since, and tracks the agent's card", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("TASK_ASSIGNED", { detail: "t1", summary: "task:t1 → dev: Do it" }));
    state(store).ingest(
      sig("TASK_MOVED", {
        ts: "2026-01-01T10:00:00",
        detail: { task_id: "t1", from: "todo", to: "in_progress", reason: "start", owner: "dev", round: 0 },
      }),
    );
    expect(state(store).cards.t1).toMatchObject({ column: "in_progress", since: "2026-01-01T10:00:00" });
    expect(state(store).agents.dev.card).toBe("t1");
    state(store).ingest(
      sig("TASK_MOVED", { detail: { task_id: "t1", from: "in_progress", to: "review", reason: "done", owner: "dev", round: 1 } }),
    );
    expect(state(store).cards.t1).toMatchObject({ column: "review", round: 1 });
    expect(state(store).agents.dev.card).toBeUndefined();
  });

  it("TASK_MOVED for an unseen card creates it", () => {
    const store = createRunStore("r1");
    state(store).ingest(
      sig("TASK_MOVED", { detail: { task_id: "late", from: "todo", to: "done", reason: "", owner: "qa", round: 2 } }),
    );
    expect(state(store).cards.late).toMatchObject({ column: "done", round: 2, owner: "qa" });
  });

  it("TASK_THREAD appends to the card thread", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("TASK_ASSIGNED", { detail: "t1", summary: "task:t1 → dev: Do it" }));
    const entry = { task_id: "t1", kind: "note", author: "dev", text: "working", iteration: 1, data: {} };
    state(store).ingest([sig("TASK_THREAD", { detail: entry }), sig("TASK_THREAD", { detail: { ...entry, text: "more" } })]);
    expect(state(store).cards.t1.thread.map((t) => t.text)).toEqual(["working", "more"]);
  });

  it("APPROVAL_NEEDED adds and GRANTED / DENIED remove", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("APPROVAL_NEEDED", { from: "runner", summary: "run bash?", detail: "approval_id=p1; kind=tool_permission" }),
      sig("APPROVAL_NEEDED", { from: "runner", detail: "approval_id=p2; kind=heavy_tier_dispatch; estimated_cost_usd=0.42" }),
    ]);
    expect(state(store).approvals.map((a) => a.id)).toEqual(["p1", "p2"]);
    expect(state(store).approvals[1].estimatedCostUsd).toBeCloseTo(0.42);
    state(store).ingest(sig("APPROVAL_GRANTED", { detail: "approval_id=p1; kind=tool_permission; scope=once" }));
    expect(state(store).approvals.map((a) => a.id)).toEqual(["p2"]);
    state(store).ingest(sig("APPROVAL_DENIED", { detail: "approval_id=p2; kind=heavy_tier_dispatch" }));
    expect(state(store).approvals).toEqual([]);
  });

  it("a decision that arrives before its request keeps the approval out", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("APPROVAL_GRANTED", { detail: "approval_id=p9; kind=x" }),
      sig("APPROVAL_NEEDED", { from: "runner", detail: "approval_id=p9; kind=x" }),
    ]);
    expect(state(store).approvals).toEqual([]);
  });

  it("OVERSEER_INTERVENTION is recorded and the overseer's result is attached", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("OVERSEER_INTERVENTION", { from: "overseer", detail: { type: "rebrief", rationale: "stuck", needs_human: false, role: "dev" } }),
      sig("AGENT_RESULT", { from: "overseer", summary: "rebriefed dev", detail: { result: {}, cost: 0.1 } }),
    ]);
    expect(state(store).interventions).toHaveLength(1);
    expect(state(store).interventions[0].data.type).toBe("rebrief");
    expect(state(store).interventions[0].result).toBe("rebriefed dev");
  });

  it("ARTIFACT and SCREENSHOT_READY both land in artifacts", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("ARTIFACT", {
        from: "dev",
        detail: { path: "out/a.svg", kind: "svg", role: "dev", task_id: "t1", size: 10, sha256: "x", created_at: "2026-01-01", served_path: "/files/a.svg" },
      }),
      sig("SCREENSHOT_READY", { from: "qa", detail: { path: "shots/1.png", url: "/shots/1.png" } }),
    ]);
    expect(state(store).artifacts).toHaveLength(2);
    expect(state(store).artifacts[0]).toMatchObject({ source: "artifact", path: "out/a.svg", taskId: "t1", servedPath: "/files/a.svg" });
    expect(state(store).artifacts[1]).toMatchObject({ source: "screenshot", role: "qa", url: "/shots/1.png" });
  });

  it("WEB_REQUEST is logged", () => {
    const store = createRunStore("r1");
    state(store).ingest(
      sig("WEB_REQUEST", {
        from: "dev",
        detail: { tool: "web-fetch", url: "https://x.test", final_url: "https://x.test/", status: 200, cache_hit: false, bytes: 1200, refused: null },
      }),
    );
    expect(state(store).web).toEqual([
      expect.objectContaining({ tool: "web-fetch", status: 200, bytes: 1200, role: "dev", refused: null }),
    ]);
  });

  it("HUMAN_NEEDED sets the banner and pauses the agent; HUMAN_RESPONDED clears both", () => {
    const store = createRunStore("r1");
    state(store).ingest([sig("STARTED", { from: "dev" }), sig("HUMAN_NEEDED", { from: "dev", summary: "which db?" })]);
    expect(state(store).humanNeeded).toMatchObject({ role: "dev", summary: "which db?" });
    expect(state(store).agents.dev.state).toBe("waiting");
    state(store).ingest(sig("HUMAN_RESPONDED", {}));
    expect(state(store).humanNeeded).toBeNull();
    expect(state(store).agents.dev.state).toBe("running");
  });
});

describe("run event store: seedCards (REST)", () => {
  const wire = (id: string, column: "todo" | "done", extra = {}) => ({
    id, title: `Card ${id}`, owner: "dev", column, kind: "code", size: "S", priority: 2,
    review_round: 0, acceptance: ["works"], depends_on: [], column_since: "2026-01-01", thread: [], ...extra,
  });

  it("fills in cards the socket has not seen", () => {
    const store = createRunStore("r1");
    state(store).seedCards([wire("t1", "todo")]);
    expect(state(store).cards.t1).toMatchObject({ title: "Card t1", acceptance: ["works"], priority: 2, column: "todo" });
  });

  it("completes a stub from TASK_ASSIGNED with REST fields", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("TASK_ASSIGNED", { detail: "t1", summary: "task:t1 → dev: short" }));
    state(store).seedCards([wire("t1", "todo")]);
    expect(state(store).cards.t1.stub).toBeUndefined();
    expect(state(store).cards.t1.acceptance).toEqual(["works"]);
  });

  it("keeps live column and thread when signals already moved the card", () => {
    const store = createRunStore("r1");
    state(store).seedCards([wire("t1", "todo")]);
    state(store).ingest(
      sig("TASK_MOVED", { detail: { task_id: "t1", from: "todo", to: "done", reason: "", owner: "dev", round: 1 } }),
    );
    state(store).seedCards([wire("t1", "todo", { title: "Renamed" })]);
    expect(state(store).cards.t1).toMatchObject({ column: "done", round: 1, title: "Renamed" });
  });
});

describe("run event store: performance", () => {
  function makeSignals(n: number): RawSignal[] {
    const out: RawSignal[] = [];
    for (let i = 0; i < n; i++) {
      const role = `agent${i % 8}`;
      switch (i % 5) {
        case 0:
          out.push(sig("STARTED", { from: role, id: `p${i}` }));
          break;
        case 1:
          out.push(sig("TOOL_CALL", { from: role, id: `p${i}`, detail: { tool: "bash", status: "ok", input: "ls", duration_ms: 2 } }));
          break;
        case 2:
          out.push(sig("AGENT_RESULT", { from: role, id: `p${i}`, detail: { result: { ok: true }, cost: 0.01 } }));
          break;
        case 3:
          out.push(sig("TASK_MOVED", { id: `p${i}`, detail: { task_id: `t${i % 40}`, from: "todo", to: "in_progress", reason: "", owner: role, round: 0 } }));
          break;
        default:
          out.push(sig("PROTOCOL", { from: role, id: `p${i}` }));
      }
    }
    return out;
  }

  it("ingests 2,000 signals as one batch in under 200 ms", () => {
    const signals = makeSignals(2000);
    const store = createRunStore("perf");
    const t0 = performance.now();
    state(store).ingest(signals);
    const ms = performance.now() - t0;
    expect(state(store).signals).toHaveLength(2000);
    expect(ms).toBeLessThan(200);
  });

  it("ingests 2,000 signals one at a time in under 200 ms", () => {
    const signals = makeSignals(2000);
    const store = createRunStore("perf");
    const t0 = performance.now();
    for (const s of signals) state(store).ingest(s);
    const ms = performance.now() - t0;
    expect(state(store).signals).toHaveLength(2000);
    expect(state(store).agents.agent0.spawns).toBeGreaterThan(0);
    expect(ms).toBeLessThan(200);
  });
});

describe("run event store: review rounds and blocked-on (cockpit)", () => {
  it("tracks the highest REJECTED round per reviewer>author edge", () => {
    const store = createRunStore("r1");
    state(store).ingest([
      sig("REJECTED", { from: "reviewer", detail: { task_id: "T1", authors: ["dev", "ui"], round: 1 } }),
      sig("REJECTED", { from: "reviewer", detail: { task_id: "T1", authors: ["dev"], round: 2 } }),
      sig("REJECTED", { from: "reviewer", detail: { authors: ["dev"], round: 1 } }),
    ]);
    expect(state(store).reviews["reviewer>dev"]).toMatchObject({ round: 2, author: "dev", reviewer: "reviewer" });
    expect(state(store).reviews["reviewer>ui"].round).toBe(1);
  });

  it("records BLOCKED on a role and clears it when the role runs again", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("BLOCKED", { from: "dev", detail: { task_id: "T1", on: "api", need: "schema" } }));
    expect(state(store).blockedOn.dev).toMatchObject({ on: "api", need: "schema", taskId: "T1" });
    state(store).ingest(sig("STARTED", { from: "dev" }));
    expect(state(store).blockedOn.dev).toBeUndefined();
  });

  it("keeps the last AGENT_RESULT signal id on the agent", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("AGENT_RESULT", { from: "dev", id: "res1", detail: { result: {}, cost: 0.1 } }));
    expect(state(store).agents.dev.lastResultSignalId).toBe("res1");
  });
});

describe("run event store: seedCosts (gateway poll)", () => {
  it("shows gateway spend during a turn, before AGENT_RESULT", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("STARTED", { from: "dev" }));
    state(store).seedCosts([{ role: "dev", cost_usd: 0.28 }, { role: "reviewer", cost_usd: 0.01 }]);
    expect(state(store).costs).toEqual({ dev: 0.28, reviewer: 0.01 });
    expect(state(store).agents.dev.cost).toBe(0.28);
  });

  it("never lowers a total: AGENT_RESULT and polls both only raise it", () => {
    const store = createRunStore("r1");
    state(store).ingest(sig("AGENT_RESULT", { from: "dev", detail: { cost: 0.3 } }));
    const before = state(store).costs;
    state(store).seedCosts([{ role: "dev", cost_usd: 0.2 }]);
    expect(state(store).costs).toBe(before);
    state(store).seedCosts([{ role: "dev", cost_usd: 0.4 }]);
    state(store).ingest(sig("AGENT_RESULT", { from: "dev", detail: { cost: 0.35 } }));
    expect(state(store).costs.dev).toBe(0.4);
    expect(state(store).agents.dev.cost).toBe(0.4);
  });
});

describe("run event store: plan checkpoint (worker context plan H5)", () => {
  it("marks a plan-review pause with its cards", () => {
    const store = createRunStore("r1");
    state(store).ingest([sig("HUMAN_NEEDED", { from: "planner", summary: "Review the plan before work starts",
      detail: { kind: "plan_review", cards: ["T1", "T2"], design_changes: 0, notes: "" } })]);
    expect(state(store).humanNeeded).toMatchObject({ kind: "plan_review", cards: ["T1", "T2"] });
    state(store).ingest([sig("HUMAN_NEEDED", { from: "dev", summary: "which db?", detail: "plain text" })]);
    expect(state(store).humanNeeded?.kind).toBeUndefined();
  });
});
