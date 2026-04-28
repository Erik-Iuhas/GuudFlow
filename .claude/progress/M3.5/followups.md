# M3.5 — Follow-ups + Forward Compatibility

Things intentionally **not** in M3.5. Two purposes:

1. **Forward-compat clarity.** Plans past M4 already exist on Erik's
   local machine. This list keeps M3.5 from claiming surface that
   later milestones may want.
2. **Deferred items registry.** Things that came up during M3.5 design
   and need to land somewhere — but not here.

When extending the codebase later, check this list before adding to
M3.5's surface.

---

## Out of scope for M3.5

### Studio task-board UI

M3.5 ships only `GET /api/runs/{id}/desks` and the existing WebSocket
event stream. **No studio components, no React views, no panels.**
This is the largest carved-out scope item — historically studio work
has been ~50% of phase token cost.

### New agent archetypes

No `Planner`, no `Scrum Master`, no `Product Owner`, no `Project
Manager`. Planning behaviour is added to the four existing planning
archetypes (`tech_lead`, `architect`, `requirements_writer`, `meta`).

If a future milestone wants a dedicated planner archetype, it can
reuse the `task:assign` scope wholesale.

### Dynamic dependency-graph mutation

`AgentConfig.depends_on` is set at proposal time and frozen. Tasks do
not let a planner change which agents run or their dependency order.

### Cross-run task carry-over

Tasks die with the run. Continuous-app-building (Phase K) may want
project-level tasks that survive into the next run. Not M3.5.

### Task estimation / cost forecasting

Tasks have no `estimated_tokens`, no `estimated_cost`, no `priority`.
The cost estimator (`core/cost_estimate.py`) is unaware of tasks.

### Memory layer integration

`Persona.recall(task_description)` does not see Tasks. Tasks are
ephemeral; episodes/patterns/antipatterns are persistent. Wiring tasks
into memory is a memory-layer addition, owned elsewhere.

### Reviewer-side automated cross-check

The reviewer's job after M3.5 is unchanged — they use
`git-changed-files` + `git-diff path=<file>` to verify work, with
`task.references` and `task.acceptance_criteria` as anchors. **No
new automated tooling for the reviewer.** If we want a "did the diff
touch every referenced file" check to fire automatically, that's a
later milestone.

### MCP integration for tasks

External systems (Jira, Linear, GitHub Issues) might want to inject
tasks. Possible via an MCP server, but not in M3.5. The
`task:assign` RBAC scope and the `assign-task` tool would be reused.

### Studio user-board visualisation

The user-comment-routing in chunk 6 is backend-only. The studio's
existing user-board view continues to display USER_COMMENT signals.
Later studio work can add "this comment was picked up by tech_lead and
became task:abc123" affordance.

---

## Hooks left explicitly open for M4+

Things M3.5 designs *toward* without implementing:

### `Task.priority` field

Not in chunk 1's dataclass, but the FIFO-only desk renderer (chunk 4)
is structured so adding priority later is a one-method change in
`Workspace.desk_for(role)` — sort by priority, break ties by FIFO.

### Sub-sub-tasks (depth > 2)

Chunk 3 enforces depth ≤ 2 with a clear error message. M4+ can lift
this if real workflows demand it; the data model already supports
arbitrary depth via `parent_task_id`, only the runner walker is
capped.

### `TASK_DEFERRED` lifecycle state

Currently a blocked task that becomes infeasible needs to be
`CANCELLED` by the planner. M4+ may want a softer "defer to next
run" state once cross-run carry-over exists.

### Task-aware quip generation

The `QuipEngine` currently fires off `Quip` objects from agent
signals. It would be natural to quip on `TASK_COMPLETED` with the
task's goal — but that's a quip-engine extension and benefits from
M4+ studio surfacing.

### Persona reputation bucket per task type

`ReputationBucket.per_domain` is currently keyed by agent role. A
later milestone may want to roll up reputation per task type
(e.g. "Mike completes 80% of `auth-related` tasks on first
iteration"). Tasks already carry enough metadata for this aggregation
to be done post-hoc.

---

## Naming reservations

Names M3.5 explicitly does **not** consume so M4+ has them available:

- `core/task_planner.py` — left empty for a future dedicated
  planner module.
- `agents/builtin/planner.py` — left empty for a future planner
  archetype if one is hired.
- `data/projects/{id}/backlog.md` — left empty; Phase K's
  `roadmap.md` is the only project-level free-form doc M3.5 reads.
- `SCOPE`, `BACKLOG`, `SPRINT` protocol message kinds — not
  introduced in chunk 2.
- `task:read`, `task:close`, `task:cancel` RBAC scopes — only
  `task:assign` is added. Read access is implicit (the desk is in
  the agent's prompt); close/cancel use existing planner privileges.

---

## Rollout / migration notes

### Existing runs pre-M3.5

Loaded via `run_loader.restore_workspace`, the new fields default to
empty:

- `workspace.tasks = {}`
- `workspace.task_queues = {}`
- All desks render empty → `INCOMING WORK` block identical to M1
  output.

No migration script needed.

### Existing personas

`Persona` data unaffected. Episodes / patterns / reputation continue
exactly as before. Tasks are not persisted to persona memory.

### Existing tests

The plan was specifically structured so chunk 1 changes only add
fields/methods to `Workspace`. Existing `tests/core/test_workspace.py`
must continue to pass without edits. Each chunk's test gate checks
this.

### Documentation sync points

After milestone completion:

- `README.md` — status line update (see chunks.md DoD).
- `DESIGN.md` — addendum §38.12 (M3.5 — Per-agent task desks +
  scope envelopes).
- `docs/ARCHITECTURE.md` — refactor log entry.
- `docs/diagrams/04-class-agents.md` — `Task` + `TaskRef` adjacent
  to `StoryState` in the persona-and-agent class diagram.
- `docs/diagrams/06-sequence-run-lifecycle.md` — add the planner-
  pickup variant as a sequence alternate.

---

## When to revisit this file

Update this file when:

1. M4+ lands — strike through items as they're picked up.
2. A bug surfaces that traces to "we should have done X in M3.5" —
   add to Out-of-scope with a justification.
3. The Open-questions section in `decisions.md` gets answered —
   migrate the answer here as a deferred-but-now-claimed item.
