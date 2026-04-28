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

## Post-implementation — things worth noting

Surfaced during the chunks 1–8 review. Captured here so M4+ doesn't
re-discover them and so reviewers know what was deliberately left.

### Documentation sync not yet done

The "Definition of done" in `chunks.md` calls for these
documentation updates; they were skipped to keep the implementation
PR focused on code. Status:

- `README.md` — status line not yet updated to mention M3.5.
- `DESIGN.md` — addendum §38.12 (M3.5 — Per-agent task desks +
  scope envelopes) not yet written.
- `docs/ARCHITECTURE.md` — refactor log entry for M3.5 not added.
- `docs/diagrams/04-class-agents.md` — `Task` + `TaskRef` not yet
  drawn adjacent to `StoryState`.
- `docs/diagrams/06-sequence-run-lifecycle.md` — planner-pickup
  variant not yet added as a sequence alternate.

These are doc-only, no code risk. Land in a follow-up commit
before merging M3.5 to main.

### Chunk-3 fix-along bundled, not split

Commit `e517dcc` (M3.5.3) also added two pre-existing tools to
`_build_local_registry`: `BoardMessage` and `GitChangedFiles`. Both
were already instantiated per agent in `_instantiate_local_tools`
but never registered, meaning their manifest entries were silently
empty for any non-baseline scope check.

This is a real fix unrelated to M3.5 — the kind of thing that
should be its own commit titled `fix(runner): register
BoardMessage + GitChangedFiles`. Splitting now requires interactive
rebase + force-push of an already-pushed feature branch; held off
on that to avoid history rewrite. **Policy going forward**:
drive-by fixes get their own commit even when discovered mid-chunk.

### Manual smoke test recommended before merge

The desk rendering and `closes:` flow have unit + integration test
coverage but have not been exercised against a real LLM. Suggested
smoke-test scenario before merging M3.5:

1. Create a project with `architect → developer → code_reviewer`.
2. Architect emits an `assign-task` call decomposing the goal.
3. Verify the developer's iteration sees the desk block.
4. Verify the developer's `[COMMIT]` includes a `closes:` field.
5. Verify the runner posts `TASK_COMPLETED` and the reviewer
   cross-checks against `acceptance_criteria`.

Cost: ~$0.50–$2 of LLM time on Sonnet. Catches prompt-shape issues
that no unit test can — especially whether the LLM actually emits
the `closes:` trailer reliably given the layered prompt.

### `_rebuild_tasks_from_board` is lossy on fields not encoded in signals

The fallback path in `core/run_loader._rebuild_tasks_from_board` —
used when `tasks.jsonl` is absent but `board.jsonl` carries TASK_*
events — recovers Task identity, assignee, status, and timing. It
**loses**:

- `acceptance_criteria` — not serialised in the signal summary.
- `references` — same.
- `out_of_scope` — same.

This is consistent with DESIGN §1.3 ("the signal trail is the
source of truth for state, the dedicated jsonl is an optimization,
not the source of truth"). The lost fields are *content*, not
*state*.

Practical impact: only relevant when a run crashed AND the task
persister never wrote AND the run is resumed. Uncommon. Reviewers
on the resumed run will see bare-bones tasks (no acceptance
criteria to check against) for those entries; they fall back to
their normal git-diff review behaviour.

If this becomes painful, options:

- (a) Fatten the `TASK_ASSIGNED` signal `detail` field to include
  the full Task payload as JSON. Cheap, ~50 tokens per task.
- (b) Make tasks.jsonl write contractually mandatory at run start
  (currently best-effort), so the fallback path becomes truly a
  last-ditch recovery rather than the expected behaviour.

Both deferred to whoever first hits the issue.

---

## When to revisit this file

Update this file when:

1. M4+ lands — strike through items as they're picked up.
2. A bug surfaces that traces to "we should have done X in M3.5" —
   add to Out-of-scope with a justification.
3. The Open-questions section in `decisions.md` gets answered —
   migrate the answer here as a deferred-but-now-claimed item.
4. The post-implementation notes above land — strike through as
   each one is resolved (doc updates merged, smoke test run, etc.).
