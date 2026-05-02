# Track 7 — Subtask decomposition (intra-run)

**Status:** In progress. Sonnet-executable.
**Parent plan:** `master_subtasks_dispatch_t7_t8_t12.md` (Phase 2)
**Token budget:** 300–500k. Stop and reassess if over 600k.
**Depends on:** Nothing hard. Builds on the existing M3.5 task desk
system ([core/workspace.py:225-650](core/workspace.py:225)) which
already has `parent_task_id`, `child_tasks()`, and the cascade-closure
logic at [core/runner.py:2158-2178](core/runner.py:2158).
**Unblocks:** Track 8 (subtask context hot-swap — the active-subtask
boundary is what hot-swap stashes around).

---

## Why

**Hybrid model — both delegator AND receiver can decompose, captured
2026-05-02 after design discussion.** Three flows need first-class
support, none of them lone:

- **Self-decomposition (no formal task).** The `codebase_analyst` on
  QuickRefs-web had no task on its desk — just the project goal. The
  goal was too big to handle in one shot. Today the agent has no way
  to break it up; tomorrow it should.
- **Delegator chunking at assignment time.** When a delegator
  (architect, tech_lead) calls `assign-task`, they often know the
  work has 3-4 chunks. Today they have to call `assign-task` then
  immediately `decompose-task` as two separate moves; should be one
  atomic action via `assign-task(subtasks=[...])`.
- **Receiver further-decomposition.** A worker who got assigned a
  task decides it's still too big. Same `decompose-task` tool, just
  with an explicit `parent_task_id` pointing at the assigned task.

**Why both, not just one** (transcript 2026-05-02): trusting delegators
alone leaves the codebase_analyst-style "first agent in the run, no
delegator above me" case unsolved. Trusting receivers alone fails
when the worker is a small-tier model that overestimates its capacity.
With hybrid, smart delegators can pre-chunk for dumb workers, and
smart workers (or workers operating on the project goal directly) can
self-decompose. The receiver-side path also lets workers learn from
their own runs: as memory accumulates patterns about "tasks of this
shape need 4 subtasks," the agent's own `decompose-task` calls get
better over time.

Mechanism: `decompose-task` tool (receiver-side) + `assign-task`
extension (delegator-side). Runner creates child Tasks, advances
active subtask on COMMIT closures. The existing cascade closure logic
([core/runner.py:2158-2178](core/runner.py:2158)) handles parent
auto-close once all children terminal — no new closure code.

The synthetic root + active-subtask layer also gives Track 11 (worker
cognition view) something concrete to render — "currently on subtask
T3" instead of "currently calling tools."

---

## Outcome

- New tool `decompose-task(subtasks: list[{goal, acceptance}], parent_task_id: str | None = None)`.
  No parent → attach to caller's synthetic root for this run. Parent
  given → must be a task assigned to caller and have no children.
- `assign-task` gains optional `subtasks: list[{goal, acceptance}]`.
  When present, runner creates parent + children atomically (all
  assigned to the same assignee).
- **Synthetic root task per agent role per run.** At run start
  (after workspace init, before scheduler), runner creates one Task
  per role with `task_id="root:{role}"`, `goal=project.goal`,
  `assigned_to=role`, `status=IN_PROGRESS`. Acts as the **default
  parent for `decompose-task` when no explicit parent is given** —
  not a wrapper around all of the agent's work. Tasks created via
  `assign-task` remain top-level (siblings to the root, not children
  of it) so cascade closure semantics stay simple.
- Runner advances the agent through subtasks: when one is
  COMPLETE / BLOCKED / SAT_OUT / CANCELLED, the next PENDING child
  of the same parent becomes active via the existing
  `mutate_task("IN_PROGRESS")` path. `workspace.active_subtask[role]`
  tracks the current one.
- Agent sees the active subtask in their context via a new
  `effective_system_prompt` Layer 4.7 — "ACTIVE SUBTASK".
- Cascade closure (already exists) auto-closes the parent once all
  children terminal. Synthetic root closure isn't load-bearing for
  run-end — the scheduler ends the role when no more work is
  dispatchable, same as today.
- Loop-guard treats subtask transitions as forward progress.

### Depth structure — both paths fit in depth=3

| Path | depth 1 | depth 2 | depth 3 |
|---|---|---|---|
| Self-decompose | `root:{role}` | child of root (via `decompose-task`) | further-decomp by receiver |
| Delegator pre-chunks | assigned task (top-level, no parent) | child of assigned task (via `assign-task subtasks=[]`) | further-decomp by receiver |
| Plain assign | assigned task (top-level) | n/a | n/a |

Synthetic root is depth=1 because it has no parent. The depth-3 cap
covers every realistic flow without further nesting (which would
encourage unhelpful work fragmentation).

## Files to modify

| File | Change |
|---|---|
| `core/workspace.py` Task depth | Lift the depth-2 cap (line 563-566) to depth-3. Synthetic root is depth-1, decomposed subtasks are depth-2, further decomposition is depth-3. |
| `core/workspace.py` `Workspace` | New `active_subtask: dict[str, str] = field(default_factory=dict)` — maps role → task_id. `mutate_task` updates it on START / COMPLETE / BLOCKED / CANCELLED transitions (set on START, clear on terminal). |
| `core/runner.py` synthetic root | New method `_create_synthetic_root_tasks()` called from `__init__` after workspace setup. Creates one root Task per role with `task_id=f"root:{role}"`, `goal=self.project.goal`, `status="IN_PROGRESS"`, `assigned_to=role`, `created_by="runner"`. Skip if a task with that id already exists (resumed runs). Posts TASK_ASSIGNED + TASK_STARTED signals so resume replay works. |
| `agents/tools/decompose_task.py` | **New.** `DecomposeTaskTool`. Inputs: `subtasks: list[{goal, acceptance}]`, optional `parent_task_id`. Logic: (1) resolve parent → caller's `root:{role}` if not provided; (2) refuse if parent unknown / not assigned to caller / already has children; (3) batch-create child Tasks via `workspace.add_task`; (4) return `{ok, subtask_ids}`. RBAC scope: `decompose-task` (write — but inert outside an active run, low blast radius). |
| `agents/tools/board.py` `AssignTaskTool` | Extend `input_schema` with optional `subtasks: list[{goal, acceptance}]`. When present: create parent task + children atomically (all `assigned_to=assignee`). Children inherit story_id. Empty subtasks list rejected. |
| `core/runner.py` task-advance loop | Inside `_handle_task_closures`, after a child task closes, look up the next PENDING child of the same parent and post `TASK_STARTED` for it. Ordering: FIFO over creation. Single source of truth: the `mutate_task("IN_PROGRESS")` call sets `active_subtask[role]`. |
| `agents/base.py` `effective_system_prompt` | New Layer 4.7 — "ACTIVE SUBTASK". Renders the subtask's `goal` + `acceptance` if `workspace.active_subtask[role]` resolves to a Task. Returns `(text, tokens)` matching Track 3d.2 contract; tokens accumulate onto `_current_metrics.relevant_files_tokens` (closest existing bucket; subtask context is task-relevant by definition). |
| `core/runner.py` `_resolve_in_place_working_dir` | No change — but Track 8 will read `_current_agent_messages` boundaries via the active_subtask transitions established here. Mention only. |
| `core/protocol.py` | No change — `closes: task:<id>` syntax already supports child task IDs. |
| `worker/job_runner.py` `serialise_project` | No change — synthetic root creation is runtime-only; not persisted to project config. |
| `tests/agents/tools/test_decompose_task.py` | **New.** (a) Self-decompose with no parent attaches to synthetic root; (b) explicit parent happy path; (c) refuses if parent unknown / not own / has children; (d) refuses empty subtask list; (e) siblings preserve creation order. |
| `tests/agents/test_assign_task_tool.py` | Extend with: `assign-task` with `subtasks` field creates parent + children atomically; assignee owns all; empty subtasks rejected. |
| `tests/integration/test_subtask_advance_in_run.py` | **New.** Three flows end-to-end with mocked LLM: (a) self-decompose against synthetic root → close subtask 1 → active_subtask flips to subtask 2; (b) delegator assigns with subtasks list → assignee sees parent + children atomically; (c) receiver further-decomposes assigned task → depth-3 honored. Each closes via COMMIT, parent auto-closes, signals on board. |
| `tests/core/test_runner_desk.py` | Extend desk-render test — confirm 3-deep indentation renders cleanly; synthetic root visible. |
| `tests/core/test_runner.py` | New: every run creates exactly one synthetic root per agent role; resumed runs (replay) don't double-create. |
| `tests/core/test_task_model.py` | Update existing depth-cap test from "depth 2 max" to "depth 3 max"; add a depth-4 reject case. |

## Tool surfaces (locked)

```python
# decompose-task — agent breaks current scope into subtasks
{
  "name": "decompose-task",
  "priority": 120,                  # coordination band, same as assign-task
  "when_to_use": (
    "the work in front of you is too big for one commit — break it into "
    "2-N named subtasks; the runner advances you through them in order."
  ),
  "input_schema": {
    "type": "object",
    "required": ["subtasks"],
    "properties": {
      "subtasks": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["goal"],
          "properties": {
            "goal":       {"type": "string"},
            "acceptance": {"type": "array", "items": {"type": "string"}},
          },
        },
      },
      "parent_task_id": {
        "type": "string",
        "description": "task on your desk to decompose; defaults to your run's synthetic root",
      },
    },
  },
  # returns: {"ok": true, "subtask_ids": [str, ...], "parent_task_id": str}
  # or       {"error": str}
}

# assign-task — extended with subtasks
{
  "input_schema": {
    "type": "object",
    "required": ["assignee", "goal"],
    "properties": {
      "assignee":     {"type": "string"},
      "goal":         {"type": "string"},
      "acceptance":   {"type": "array", "items": {"type": "string"}},
      "out_of_scope": {"type": "array", "items": {"type": "string"}},
      "parent_task":  {"type": "string"},
      "story":        {"type": "string"},
      "references":   {"type": "array"},
      "subtasks": {
        "type": "array",
        "description": (
          "optional list of {goal, acceptance} chunks. When present the "
          "runner creates the parent + children atomically; assignee gets "
          "the parent IN_PROGRESS and the children PENDING in order."
        ),
        "items": {
          "type": "object",
          "required": ["goal"],
          "properties": {
            "goal":       {"type": "string"},
            "acceptance": {"type": "array", "items": {"type": "string"}},
          },
        },
      },
    },
  },
  # returns extends to {"ok": true, "task_id": str, "subtask_ids": [str, ...]}
}
```

## Layer 4.7 — ACTIVE SUBTASK render (locked)

```
ACTIVE SUBTASK (commit closes this when done):

  task: <subtask_id>  (parent: <parent_id>)
  goal: <subtask.goal>
  acceptance:
    - <line 1>
    - <line 2>
```

Only renders when `workspace.active_subtask[role]` resolves to a
non-terminal Task. Suppresses cleanly otherwise (one-line "no active
subtask" header is unnecessary noise).

## Implementation order (fine-grained)

1. **Lift depth cap to 3** in `core/workspace.py` + update existing
   tests. Smallest possible change, gates everything else.
2. Add `Workspace.active_subtask: dict[role, task_id]`. Wire
   `mutate_task` to set on START, clear on terminal status. Tests for
   the dict update path.
3. Synthetic root: `AgentRunner._create_synthetic_root_tasks()` called
   from `__init__`. Idempotent (skip if `root:{role}` exists). Test:
   resumed run doesn't double-create.
4. `decompose-task` tool: file + class + RBAC scope registration.
   Tests: self-decompose, explicit parent, refusals.
5. Extend `AssignTaskTool` with `subtasks` field + atomic
   parent+children create. Tests: extend existing tool tests.
6. Runner task-advance: inside `_handle_task_closures` after child
   close, find next PENDING sibling and `mutate_task("IN_PROGRESS")`.
   This drives `active_subtask` automatically.
7. `effective_system_prompt` Layer 4.7. Returns `(text, tokens)`;
   accumulates onto `_current_metrics.relevant_files_tokens`.
8. Integration test: 3 flows end-to-end with mocked LLM.
9. Full sweep + commit per the commit plan below.

## Acceptance criteria

1. **Synthetic root.** Every agent role has exactly one root task on
   their desk at run start: `task_id=root:{role}`, `status=IN_PROGRESS`,
   `goal=project.goal`. Resumed runs reuse the existing one (no
   double-create).
2. **Self-decompose.** `decompose-task(subtasks=[...])` with no parent
   attaches children to the caller's synthetic root. All children
   `assigned_to=caller`, `status=PENDING`, parent unchanged.
3. **Delegator chunking.** `assign-task(assignee="dev", goal="X",
   subtasks=[{goal:"a"}, {goal:"b"}])` creates parent X + children
   a, b atomically. All `assigned_to="dev"`. Parent IN_PROGRESS,
   children PENDING.
4. **Receiver further-decompose.** Assignee can further-decompose the
   task they got — depth-3 honored, depth-4 rejected.
5. **Subtask advance.** After agent closes subtask 1 via
   `[COMMIT]\n  closes: task:s1`, `active_subtask` flips to subtask 2
   (next PENDING sibling) and the next prompt build surfaces subtask
   2's goal under Layer 4.7.
6. **Cascade closure.** Closing the last child auto-closes the parent
   (already-existing logic). Synthetic root closure is a no-op for
   run-end logic — scheduler still decides when the role is done.
7. **Refusal cases.** `decompose-task` returns `{error}` if explicit
   parent is unknown, not the caller's, or already has children.
   `assign-task` with `subtasks` rejects empty subtask lists.
8. **Loop guard.** A 5-subtask decomposition gets the guard's
   wall-clock budget extended proportionally — each subtask
   transition resets the iteration-since-progress counter (verify
   existing behavior, no new code).
9. **Layer 4.7 metrics.** When active subtask is set, the next
   `effective_system_prompt()` build adds tokens to
   `_current_metrics.relevant_files_tokens`. Suppresses cleanly when
   no active subtask.

## Files NOT touched (intentional)

- `core/protocol.py` — no new block kinds. Subtasks reuse COMMIT.
- `core/task_summarizer.py` — Track 12's `summarise-subtask` wrapper
  is what Track 8 will use to summarise closed subtasks; this track
  doesn't touch summary plumbing.
- `agents/tools/desk_search.py` — no change. Subtasks just appear as
  more rows in the existing desk index.

## Risks

- **Depth-3 cascade.** Lifting cap from 2→3 means parent closure
  cascades through one more level. Existing test in
  `test_protocol_tasks.py` covers depth-2 cascade; will extend.
- **Synthetic root accidentally closed.** If an agent emits
  `closes: task:root:dev` directly, the root status flips to COMPLETE
  but the run does NOT end (root is intentionally not load-bearing
  for run-end logic — the scheduler decides when the role is done
  based on what's dispatchable). The agent's next iteration will see
  no active subtask and fall back to working against the project goal
  directly, same as if the root were never closed. So premature
  closure is a no-op rather than a footgun. Mitigation:
  `decompose-task` returns a warning if the agent attaches 0 children
  to root (likely a misuse).
- **Resumed-run double-creation.** Synthetic-root creation must be
  idempotent — `add_task` raises on duplicate `task_id`, so the
  runner must check first or catch ValueError. Test covers this
  explicitly.
- **Active subtask drift.** If the agent closes subtask s2 before
  starting it (closes out-of-order), `active_subtask` could point at
  a closed task. Mitigation: `mutate_task` clears the dict entry on
  any terminal transition; the next reader (Layer 4.7) checks the
  task is actually IN_PROGRESS before rendering.
- **`assign-task` with subtasks: partial failure.** If parent
  creation succeeds but the third child fails (e.g., validation),
  half-state on the desk. Mitigation: validate all subtasks BEFORE
  any `add_task` call; create parent + all children in one block,
  no partial commit.
- **Layer 4.7 token budget.** Subtask goal + acceptance can run
  long. Cap the rendered text at ~250 tokens (truncate acceptance
  lines past 5). Mirrors how teammate brief is capped.

## Commit plan

```
feat(track-7): lift task depth cap from 2 to 3
feat(track-7): Workspace.active_subtask + mutate_task hooks
feat(track-7): synthetic root task per agent role at run start
feat(track-7): decompose-task tool — intra-run subtask creation
feat(track-7): assign-task accepts subtasks list — atomic parent+children
feat(track-7): runner advances active subtask on closure
feat(track-7): Layer 4.7 — active subtask in effective_system_prompt
test(track-7): three-flows integration + tool unit tests
```

Each phase: ~50–150 LOC, ~30–80k tokens. Total estimated: ~350k of the
master-plan budget for Phase 7.

## After this lands

- Track 8 has subtask boundaries to swap on. Synthetic root means
  the codebase_analyst pattern works without any project-config
  change: agent decomposes the project goal into 3-4 subtasks, T8
  hot-swaps each one's tool history out as it closes.
- Track 11 (worker cognition view) renders meaningful state —
  "currently on subtask T3 of 4."
- Delegator agents (tech_lead, architect) can hand out chunked work
  in one move instead of two.
