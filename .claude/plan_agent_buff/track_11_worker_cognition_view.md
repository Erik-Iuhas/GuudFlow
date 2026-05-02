# Track 11 — Worker cognition view (gated behind Track 7)

**Status:** Specced. **Do not start until Track 7 (subtask decomposition) ships.**
**Parent plan:** `~/.claude/plans/i-think-right-now-linked-rabin.md`
**Token budget:** 200–350k. Stop and reassess if over 450k.
**Depends on:** Track 7 (subtask decomposition). Pairs naturally with Track 8 (subtask context hot-swap) — once subtasks have hot-swappable context, this view shows the swap happening.

---

## Why

The existing side-rail `WorkerCard` shows agent state (running / done / waiting) and a 1-signal preview. That's enough to know an agent exists; it's not enough to see **what they're currently thinking about**. When something feels stuck (e.g., the `codebase_analyst` PROTOCOL_PARSE_FAIL run), the only way to find out is to grep `iteration_metrics.jsonl` after the fact.

The point of this view is **live cognition**, not stats. Erik's framing: *"checking where it is in the current state of thinking."* Once Track 7 lands subtask decomposition, an agent's "state of thinking" is a structured object — current subtask, context grabbed for it, last tool called, next action — and that's the unit this view should render.

A pure stats dashboard would be the wrong shape. The Analytics tab already covers historical aggregates.

---

## Scope

**In scope (v1):**
- New "Workers" tab in `Run.tsx`, peer to Board / Kanban / Git / Files / Analytics.
- Per-agent panel showing **current** state:
  - Active subtask (id + one-line description)
  - Tool currently being called (name + arguments preview)
  - Last tool call result (compressed summary, ~3 lines max)
  - Next planned action (last `[STATUS]` block or current scratchpad excerpt if available)
  - Context window: which subtask's context is currently live (paired with Track 8 hot-swap)
- Click-to-expand: full subtask history for that agent in this run (chronological subtask switches).
- Live-update via the existing run WS — no polling.
- Read-only. No actions, no nudges, no "kill agent" button (those are separate concerns).

**Out of scope:**
- Historical analytics (already in Analytics tab).
- Per-tool aggregate stats (would belong in Analytics, not Workers).
- Editing subtask priority / re-ordering (operator UI; Track 10's territory).
- Multi-run comparisons.
- Filtering across all workers' tool calls in a single timeline (BoardFeed already does the cross-cutting view).

---

## Why gated behind Track 7

Without subtask decomposition, "current state of thinking" reduces to "current tool call" — which is just a slightly fancier `WorkerCard`. The interesting unit of cognition is the subtask: what is the agent currently trying to do, and how is it spending context to do it. Building this view before Track 7 means rebuilding it after.

Track 8 (subtask context hot-swap) is the natural pair: this view's "Context window: subtask X is live" line is meaningful only when context actually swaps between subtasks.

---

## Data sources (already in place — no new persistence)

| Field | Source |
|---|---|
| Agent role + state | `workspace.agent_states[role]` (live) |
| Active subtask | `workspace.tasks` + `workspace.task_queues[role]` (M3.5 desk) |
| Tool currently being called | the in-flight `LLMResponse.tool_calls` on the agent's current LLM round-trip — needs a small hook to expose live |
| Last tool result | `agent_turns.jsonl` rows for completed turns; in-memory ring buffer for the live turn |
| Last `[STATUS]` block | parsed from `agent.run()` LLM responses; existing protocol parser already handles `STATUS` kind |
| Subtask context window | Track 7/8 will expose `agent._current_subtask_context` (proposed) |

**One small backend hook needed:** an in-memory "current tool call" field on `BaseAgent` (`_current_tool_call: ToolCall | None`), set before `call_tool()` and cleared after. The runner exposes a snapshot via the existing `RunRegistry.workspaces` map, and a new WS frame `WORKER_TICK` carries it to the studio at most every 1s per agent.

---

## Files to touch (estimated)

| File | Change |
|---|---|
| `agents/base.py` | Set `self._current_tool_call` in `call_tool()` (1 LOC each side). |
| `core/runner.py` | New WS frame `WORKER_TICK` emitted by the existing per-agent observer. |
| `api/websocket.py` | Forward `WORKER_TICK` to subscribers. |
| `studio/src/views/Run.tsx` | Add "Workers" tab (between Files and Analytics). |
| `studio/src/components/WorkerCognitionView.tsx` | New: the panel itself. |
| `studio/src/api.ts` | Type definitions for the new WS frame. |
| `tests/agents/test_current_tool_call.py` | Asserts the hook sets/clears correctly. |
| `tests/api/test_websocket.py` | Asserts `WORKER_TICK` carries the right payload. |

No new endpoints — Workers tab reads from the existing run WS + `iteration_metrics.jsonl` as a fallback for completed turns.

---

## UI sketch

```
┌─ Workers ─────────────────────────────────────────────────────────────┐
│                                                                       │
│  ┌─ codebase_analyst (Nadia) — running ────────────────────────────┐  │
│  │  Subtask: T3 "Inventory Vite component imports"                 │  │
│  │  Tool now: code-outline-batch( paths=["src/components/*.tsx"] ) │  │
│  │  Last tool: fs-list("src/components") → 47 files (5.8K → 1.2K)  │  │
│  │  Next:  STATUS — "Building per-component import graph"          │  │
│  │  Context window: subtask T3 (24K tokens)                        │  │
│  │  [▾ subtask history (3)]                                        │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌─ docs_writer (Mike) — pending ──────────────────────────────────┐  │
│  │  Waiting on: codebase_analyst APPROVED                          │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

Compact, readable, no charts. Reads top-to-bottom in the order the user cares: what subtask, what tool, what next.

---

## Acceptance criteria

1. New "Workers" tab is visible and selectable in `Run.tsx` for any run with at least one agent in `workspace.agent_states`.
2. For each running agent, the panel shows:
   - The active subtask (or "no subtask" if Track 7's flow hasn't assigned one)
   - The current tool call (or "thinking" if no tool is in flight)
   - The last completed tool call's compressed summary
   - The most recent `STATUS` block, if one was emitted this iteration
3. Subtask history sub-section lists all subtask transitions for that agent in this run, chronologically, with timestamps.
4. The panel updates live via WS — no manual refresh needed. Confirmed by simulating a tool-call-started/ended sequence in a test.
5. Read-only — no buttons that mutate state.
6. Older runs (no subtask data on disk) render an "Awaiting Track 7 data" placeholder rather than crashing.

---

## Risks / things to watch

- **WS frame frequency.** Throttle `WORKER_TICK` to ≤1/s per agent or the studio drowns mid-run. Existing tick mechanisms in `RunRegistry` give a pattern to follow.
- **Context window display accuracy.** Until Track 8 ships, "Context window: subtask T3" will be aspirational — there's no real swap happening. The panel should label this honestly ("subtask in scope" not "context = 24K"); honest line until 8 lands real numbers.
- **Long-running tool calls.** Some tool calls (e.g., a slow web-fetch) take 30+ seconds. The panel should show elapsed time on the current call so the user can tell "this is slow" from "this is wedged."
- **Privacy / token leakage.** The panel surfaces tool arguments — anything sensitive (e.g., API tokens passed via the credentials broker) must be redacted before display. Reuse existing redaction in `agent_turns.jsonl` writer.

---

## After this lands

The pair of Track 7 → Track 8 → Track 11 is the "thinking transparency" arc:
- 7 — agents decompose work into subtasks
- 8 — context follows the active subtask, doesn't bleed across them
- 11 — user can see the cognition happening in real time

This is the UX argument for Track 8 in particular: hot-swapped context is invisible until you can watch it swap.
