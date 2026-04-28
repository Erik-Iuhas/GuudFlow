# M3.5 — Implementation Chunks

Eight chunks, ordered so each is independently mergeable, individually
testable, and reversible. Estimated total: 2–3 days.

Each chunk lists files, public surface, test gate, and a draft commit
message in the project's existing convention. Test gates must be green
before moving to the next chunk.

---

## Chunk 1 — `Task` dataclass + workspace plumbing

**Why first.** Everything depends on the data model. Land it with
empty desks; nothing else changes.

**Files**
- `core/workspace.py`
  - Add `TaskStatus`, `TaskRefKind` Literals.
  - Add `TaskRef`, `Task` dataclasses.
  - Extend `Workspace`: `tasks: dict[str, Task]`,
    `task_queues: dict[str, list[str]]`,
    `_task_persister: Callable | None`.
  - Add `Workspace.add_task`, `mutate_task`, `desk_for`,
    `child_tasks`.
- `core/signals.py`
  - Add `TASK_ASSIGNED`, `TASK_STARTED`, `TASK_COMPLETED`,
    `TASK_BLOCKED`, `TASK_CANCELLED`, `TASK_OUT_OF_SCOPE` to
    `SignalType`.

**Public surface (importable)**
- `from core.workspace import Task, TaskRef, TaskStatus, TaskRefKind`
- `from core.signals import SignalType` (new members visible)

**Tests** — `tests/core/test_task_model.py` (new)
- `Task` dataclass roundtrip via dict.
- `Workspace.add_task` adds to `tasks` AND appends to
  `task_queues[assignee]` AND posts `TASK_ASSIGNED`.
- `Workspace.mutate_task` posts the right signal and updates dict
  atomically.
- `Workspace.desk_for(role)` filters by status.
- `Workspace.child_tasks(parent)` returns child tasks in FIFO order.
- Empty workspace: no `task_*` fields cause failures elsewhere
  (run existing `test_workspace.py` unchanged).

**Commit**
> `feat(core): Task dataclass + per-agent desk plumbing (M3.5.1)`

---

## Chunk 2 — Protocol wire format

**Files**
- `core/protocol.py`
  - Add `render_assign_task(...)` + parser.
  - Add `render_task_started`, `render_task_completed`,
    `render_task_blocked`, `render_task_cancelled` + parsers.
  - Extend `render_commit` with optional `closes: list[str]`
    parameter; emit `closes:` trailer line when populated.
  - Extend `_parse_commit` to pull `closes:` field into
    `ProtocolMessage.parsed["closes"]`.

**Public surface**
- `core.protocol.render_assign_task`, `render_task_*`,
  `parse_assign_task`, `parse_task_started`, etc.
- Existing `render_commit(...)` signature backwards compatible — new
  param is keyword-only with default `None`.

**Tests** — `tests/core/test_protocol_tasks.py` (new)
- `render_assign_task` roundtrips through `parse`.
- `render_commit(..., closes=["task:abc"])` parses with `closes`
  field populated.
- `render_commit(...)` without `closes` parses identically to today
  (regression guard against breaking M1 callers).
- Acceptance/refs/parent_task fields all parse.

**Commit**
> `feat(protocol): ASSIGN_TASK + TASK_* messages + COMMIT closes: (M3.5.2)`

---

## Chunk 3 — RBAC scope + `assign-task` tool

**Files**
- `core/tool_manifest.py`
  - Add `task:assign` to gating list.
- `agents/abstract.py`
  - Reviewer + Specialist `default_scopes` unchanged
    (no `task:assign`).
- `agents/builtin/tech_lead.py`,
  `agents/builtin/architect.py`,
  `agents/builtin/requirements_writer.py`,
  `agents/builtin/meta.py`
  - Append `"task:assign"` to each archetype's `default_scopes`.
- `agents/tools/board.py`
  - Add `AssignTask(BaseTool)` next to `BoardMessage`.
  - Tool calls `Workspace.add_task`, emits `ASSIGN_TASK` protocol
    message via `_emit_protocol`, returns `task_id`.
- `core/runner.py`
  - Register `AssignTask` in `_build_tool_manifest` for any agent
    whose RBAC includes `task:assign`.

**Public surface**
- `from agents.tools.board import AssignTask`
- Tool name on the wire: `assign-task` (hyphenated, matches §38.7).

**Tests** — `tests/agents/test_assign_task_tool.py` (new)
- Specialist agent calling `assign-task` raises `ToolDenied`.
- TechLead calling `assign-task` succeeds, returns `task_id`,
  emits `TASK_ASSIGNED` and `ASSIGN_TASK` (PROTOCOL) signals.
- Subtask path: assigning with `parent_task` populated; depth-3
  rejection with clear error.
- Empty `acceptance` allowed but warned (planner discipline issue,
  not a hard failure).

**Commit**
> `feat(rbac): task:assign scope + assign-task tool (M3.5.3)`

---

## Chunk 4 — Runner desk rendering

**Files**
- `core/runner.py`
  - Rename `_build_incoming_work` → `_build_iteration_context` (back
    compat alias retained).
  - Add `_render_desk_block(role: str) -> str` producing the
    `=== YOUR DESK ===` block per design §4.1.
  - Wire `_render_desk_block` ahead of the existing
    `INCOMING WORK` block.
  - Add the planner archetype prompt suffix and conditional desk-
    discipline suffix per design §5.2 / §5.3.

**Tests** — `tests/core/test_runner_desk.py` (new) +
`tests/integration/test_task_handoff_m3_5.py` (new, basic case only;
fuller integration in chunk 5).
- Empty desk → desk block omitted, INCOMING WORK identical to today
  (snapshot test against M1 output).
- 1-task desk → renders with goal/acceptance/refs.
- 2-level subtask → child rendered indented under parent.
- Mixed PENDING/BLOCKED/COMPLETE → COMPLETE omitted, BLOCKED
  separated.
- Planner archetypes: TASK ASSIGNMENT line present in their
  effective system prompt.
- Specialist with non-empty desk: DESK DISCIPLINE line present in
  user-turn.

**Commit**
> `feat(runner): render per-agent desk + planner prompt addition (M3.5.4)`

---

## Chunk 5 — Runner `closes:` bookkeeping + subtask cascade

**Files**
- `core/runner.py`
  - In `_handle_commit_signal` (existing site that processes
    `emit_commit` output): parse `closes:` field, validate per
    design §4.3, emit `TASK_OUT_OF_SCOPE` warnings for invariant
    violations, mutate task to COMPLETE on success.
  - Implement subtask cascade per design §4.4.
  - Auto-emit `TASK_STARTED` per design §4.2 (first tool call of
    iteration that references a desk task → emit; otherwise
    auto-pick FIFO PENDING).

**Tests** — extend `tests/integration/test_task_handoff_m3_5.py`
- Architect assigns → developer's COMMIT closes via `closes:` →
  Task moves to COMPLETE, `TASK_COMPLETED` signal fires.
- Closing a task assigned to another role → `TASK_OUT_OF_SCOPE`
  warning, task stays IN_PROGRESS, COMMIT itself is not blocked.
- Closing the last child of a parent task auto-closes the parent
  with `closed_by_commit` = last child's commit.
- COMMIT without `closes:` is unaffected by any task logic
  (regression guard).
- `TASK_STARTED` auto-emit on first tool call referencing a desk
  task.

**Commit**
> `feat(runner): closes: bookkeeping + subtask cascade + TASK_STARTED auto-emit (M3.5.5)`

---

## Chunk 6 — Board router (user comment → planner pickup)

**Why this chunk is gated by chunks 1–5.** The router needs Tasks
to put work on the planner's desk.

**Files**
- `core/runner.py`
  - On `USER_COMMENT` posted to board: if the team includes a
    planning archetype (priority order tech_lead > architect >
    requirements_writer > meta), select the highest available not
    currently RUNNING and:
    - Add a synthetic `Task` to their desk:
      - `task_id = f"comment_{comment_id}"`
      - `goal = "respond to user comment"`
      - `references = [story:active, comment_signal_id]`
    - Mark agent_state READY if not already RUNNING.
  - If no planning archetype is hired → fallback to today's
    `check_user_board()` flow at next step boundary.
- `agents/builtin/tech_lead.py` + planner archetypes
  - Append a one-liner to system prompt: *"User comments arriving
    on your desk are the team's interface to the user. Decide:
    answer directly, decompose into tasks for downstream agents,
    or post HUMAN_NEEDED if you can't proceed."*

**Tests** — extend `tests/integration/test_task_handoff_m3_5.py`
- USER_COMMENT with tech_lead in team → tech_lead's desk grows
  by one synthetic task; tech_lead is READY/RUNNING next pass.
- USER_COMMENT with only specialists → no synthetic task created,
  agents `check_user_board()` next step (existing behaviour).
- Priority order: when both tech_lead and architect are on
  standby, tech_lead is selected.
- USER_COMMENT during tech_lead RUNNING → falls through to next
  available planner; if none, defers to next iteration.

**Commit**
> `feat(runner): user-comment → planner pickup via task desk (M3.5.6)`

---

## Chunk 7 — Persistence + resume

**Files**
- `core/workspace.py`
  - Wire `_task_persister` analogously to `_board_persister` —
    fires on every `add_task` / `mutate_task`.
  - Persister target: `data/runs/{run_id}/tasks.jsonl` (append-only
    mutation log).
- `core/run_loader.py`
  - `restore_workspace` reads `tasks.jsonl` after `board.jsonl`,
    rebuilds `workspace.tasks` and `workspace.task_queues`.
  - Verify against the signal trail: any `TASK_*` signal in the
    board must have a matching mutation in `tasks.jsonl` (consistency
    check; if drift, signal trail wins, log warning).

**Tests** — `tests/core/test_run_loader_tasks.py` (new) +
extend `tests/worker/test_job_runner_resume.py`
- Crash mid-run with 3 PENDING tasks + 1 IN_PROGRESS → resume
  restores all 4 in correct states.
- Subtask cascade across resume boundary: parent task closes after
  resume when remaining child closes.
- Drift case: `tasks.jsonl` missing a mutation present in
  `board.jsonl` → loader rebuilds from board, logs warning.

**Commit**
> `feat(workspace): incremental task persistence + resume (M3.5.7)`

---

## Chunk 8 — API surface

**Files**
- `api/routes/runs.py`
  - `GET /api/runs/{run_id}/desks` returning desks dict +
    tasks_by_id.
  - Bearer-auth gated.
- `api/websocket.py`
  - No code changes — `TASK_*` signals already stream via the
    existing run board WebSocket. Verify with a test that
    confirms the JSON shape.
- `api/storage.py`
  - `RunStore` may need a helper to load tasks from `tasks.jsonl`
    for non-running runs.

**Tests** — `tests/api/test_desks.py` (new)
- `GET /api/runs/{id}/desks` returns 401 without bearer.
- Returns expected shape for a running run.
- Returns expected shape for a completed run (loaded from disk).
- WebSocket: `TASK_ASSIGNED` signal streams with full payload.

**Commit**
> `feat(api): GET /api/runs/{id}/desks + WS task events (M3.5.8)`

---

## Order-of-operations summary

```
1 (model) → 2 (protocol) → 3 (rbac+tool) → 4 (desk render)
            ↓
            5 (closes: + cascade)
                ↓
                6 (user-comment router) — backend feature complete
                ↓
                7 (persistence)
                ↓
                8 (api surface) — milestone complete
```

Chunks 1–6 unlock the feature. Chunks 7 and 8 are the production-
quality hardening; they can ship in any order after 6.

## Pre-flight checks (before starting chunk 1)

- [ ] All current tests green: `pytest`
- [ ] Studio typecheck green: `cd studio && npm run typecheck`
- [ ] `.claude/progress/M3.5/` plan committed and pushed
- [ ] Confirm with Erik that the protocol wire format in §2 is the
      shape we want before writing parsers

## Definition of done for the milestone

- All 8 chunks merged with green tests.
- New integration test `tests/integration/test_task_handoff_m3_5.py`
  exercises every flow listed in design §7.
- DESIGN.md addendum §38.12 added (M3.5 — Per-agent task desks).
- README.md status line updated to include M3.5.
- `docs/diagrams/04-class-agents.md` updated to include `Task` and
  `TaskRef` adjacent to `StoryState`.
- `docs/diagrams/06-sequence-run-lifecycle.md` updated with the
  planner-pickup flow as an additional sequence variant.
- `docs/ARCHITECTURE.md` refactor log gains an M3.5 entry.

## Reversal procedure

If M3.5 needs to be unwound, the operations are:

1. Remove the 6 new `SignalType` members.
2. Remove `closes:` from `render_commit` (parser already accepts
   commits without it).
3. Remove `_render_desk_block` call site from runner.
4. Remove `task:assign` scope grants.
5. Delete `data/runs/{id}/tasks.jsonl` files (no other reader
   depends on them).

Roughly 200 LOC reverted. Existing M1 INCOMING WORK behaviour
unchanged because it was preserved at every step.
