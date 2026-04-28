# M3.5 — Locked Design Decisions

Captured from the design conversation that produced this milestone.
Each decision lists the question, the chosen answer, the rationale, and
the alternative that was rejected (so future readers don't reopen
settled debate).

---

## D1 — Tasks layer on top of `depends_on`

**Question.** Should tasks *replace* the static `depends_on` DAG
(planner agents grow the graph mid-run, agents spawn dynamically) or
*layer on top* of it (DAG fixed at proposal time, tasks sequence work
within roles)?

**Decision.** Layer on top. Static DAG; tasks are intra-role work
items.

**Why.**
- Dynamic agent spawning is a money pit — agentic calls compound and
  there is no upper bound on what a planner could request.
- The scheduler stays simple. Its concurrency rules (CLAUDE.md hard
  rule 2) and dependency-resolution invariants (DESIGN §3.3) don't
  move.
- Reversal is cheap: an empty desk reproduces today's behaviour
  exactly. Projects that don't use planning archetypes don't see any
  change.

**Rejected.** Mid-run agent spawning gated by tech_lead approval. Even
gated, the cost variance was unacceptable given current pricing.

---

## D2 — RBAC scope `task:assign` restricted to planning archetypes

**Question.** Who can emit `ASSIGN_TASK`?

**Decision.** Only agents holding the `task:assign` RBAC scope. The
scope is granted by default to four archetypes: `tech_lead`,
`architect`, `requirements_writer`, `meta`. Specialist and Reviewer
archetypes do not get it.

**Why.**
- Open-to-all assignment becomes peer-thrash. With four planners and
  six specialists, anyone reassigning anyone else's queue produces
  nondeterministic behaviour the scheduler cannot reason about.
- The four planning archetypes already exist; this is RBAC, not new
  agents.
- RBAC scope strings already match tool names 1:1 (commit `0709f1b`).
  Adding `task:assign` is the same shape as adding any other scope.

**User flow corollary.** When the user posts a `USER_COMMENT` on the
board, the runner routes it to the highest-tier standby planner in the
team (priority order: `tech_lead > architect > requirements_writer >
meta`). That planner picks up the comment, decomposes it, and assigns
tasks downward. This is the "main interface to the team" behaviour
Erik described — the planner is the comms layer, not every agent
listening for user comments.

If no planning archetype is hired into the team, the user comment
falls back to current behaviour: agents check_user_board() at step
boundaries.

**Rejected.** Letting any agent emit `ASSIGN_TASK` to itself ("self-
assignment"). Sounds harmless, but it dilutes the "planner owns the
desk" invariant and makes the studio's "who assigned what" view
ambiguous.

---

## D3 — Acceptance criteria are the scope contract

**Question.** What's the minimum scope contract on a `Task`?

**Decision.** A `Task` carries:

- `goal: str` — one-sentence what to accomplish.
- `acceptance_criteria: list[str]` — named, verifiable assertions.
  Reviewers check against these instead of (or in addition to) the
  `VERIFY:` line on the agent's `COMMIT`.
- `out_of_scope: list[str]` — optional but recommended. Explicit
  exclusions catch drift early.

No DSL, no machine parser, no JSON schema — strings the LLM can
reason about and the reviewer can map to PASS/FAIL checkpoints.

**Why.**
- Free-form `goal` (what Stories carry today) is not enough — there's
  nothing for a reviewer to check against.
- Full DSL is overkill — it costs prompt-engineering work for every
  agent and freezes the format prematurely.
- Named criteria save reviewer iterations. The reviewer has a
  pre-written checklist.

**Rejected.** A structured `success_metric` field with operator/value
syntax (`response_time < 100ms`). Decided to defer — the LLM-level
validation is good enough until we have data showing where it breaks.

---

## D4 — One task, one assignee

**Question.** Can a task have multiple assignees?

**Decision.** No. Exactly one `assigned_to` role per task. Use
sub-tasks (parent_task_id) if the work needs to fan out.

**Why.**
- Multi-assignee tasks raise "who closes it" ambiguity. Sub-tasks have
  no such ambiguity — each closes independently.
- Maps cleanly to per-role desk dicts (`workspace.task_queues:
  dict[role, list[Task]]`).

---

## D5 — Tasks are children of Stories, not replacements

**Question.** Should we deprecate Stories?

**Decision.** No. A `Task` always has a `story_id` parent. Stories
remain the project-level scope envelope; tasks are the per-agent unit.

**Why.**
- Story auto-tagging on protocol messages is a useful audit trail
  even when the runner doesn't act on it.
- The runner's `active_story` pointer per role is already the natural
  place to attach the active task.

---

## D6 — Closing happens at COMMIT time, not on STATUS

**Question.** When does a task transition `IN_PROGRESS → COMPLETE`?

**Decision.** When the assignee emits a `COMMIT` with a `closes:
task_id` field. The runner validates the task is on that agent's desk
and was `IN_PROGRESS`, then mutates state + posts `TASK_COMPLETED`.

**Why.**
- Ties task closure to verifiable work product (a git commit, files,
  named checkpoints).
- Avoids "agent says it's done" without evidence.
- Doesn't add a new tool surface — agents already emit COMMIT.

**Rejected.** A separate `mark_task_complete` tool. Adds a tool slot,
duplicates logic, and lets agents close tasks without producing work.

---

## D7 — Append-only signals + per-role task dict

**Question.** How does state get mutated without violating the
append-only board invariant?

**Decision.** Same pattern as `agent_states`: post a signal
(`TASK_ASSIGNED`, `TASK_STARTED`, `TASK_COMPLETED`, `TASK_BLOCKED`,
`TASK_CANCELLED`) AND mutate the dict (`workspace.task_queues`)
through a single helper `Workspace.mutate_task(task_id, new_status,
**kwargs)`. The dict is the queryable view; the signal trail is the
source of truth for resume.

**Why.**
- `agent_states` already does this (DESIGN §3.1). Same pattern, same
  invariant.
- The signal trail is what `core/run_loader.py` replays to reconstruct
  state — tasks fall out for free.

---

## D8 — No studio UI in M3.5

**Question.** Should M3.5 ship a task-board view in the studio?

**Decision.** No. Only the read-only API (`GET /api/runs/{id}/desks`)
and a `task_event` WebSocket signal. Studio task-board UI is M4+.

**Why.**
- Studio work has historically been ~50% of phase token cost (Phase G
  was the largest). A backend-only milestone keeps M3.5 to 2–3 days.
- Erik mentioned plans past M4 already exist locally; carving studio
  work out of M3.5 leaves that lane open.

---

## D9 — Forward compatibility with M4+

**Question.** What constraints does "M4+ already exists locally"
impose on M3.5?

**Decision.** Strictly additive. No deletions, no renames, no
behaviour changes for existing flows when desks are empty. Specifically:

- No new agent archetypes — they would need to be re-reviewed against
  M4's roster.
- No changes to `Scheduler` — M4 may touch it.
- No changes to existing `Signal` types — only new ones added.
- No changes to existing protocol messages — `closes:` is a new
  optional field; old COMMITs still parse.
- No DESIGN.md edits to sections 1–37 — append-only addendum (§38)
  gets a new subsection (§38.12).

If M4 needs to revisit any M3.5 decision, it can do so without
unwinding shipped behaviour.

---

## D10 — Subtasks via `parent_task_id`

**Question.** Do tasks support decomposition into smaller units?

**Decision.** Yes. A `Task` carries an optional `parent_task_id`.
A parent task closes when all its children close. Depth is capped at
two levels (parent → child) to avoid arbitrarily deep trees that the
runner would have to walk.

**Why.**
- Real work has natural fan-out — "implement auth" → "issuer",
  "verifier", "refresh-rotation". Forcing the planner to flatten loses
  hierarchy that's useful for reviewers.
- Capping at two levels means the desk renderer is bounded:
  parent task → child tasks indented under it.
- D4's "one assignee per task" still holds — the parent has its own
  assignee (often the planner who decomposed the work, who closes it
  when children land); each child has its own assignee.

**Wire shape.** `ASSIGN_TASK` accepts `parent_task:<id>`. Closing the
last open child auto-emits `TASK_COMPLETED` for the parent (runner
side, no explicit close needed from the agent).

**Rejected.** Arbitrary depth. Cost in renderer complexity outweighs
the rare case where 3+ levels actually clarify the work.

---

## D11 — Tasks carry explicit `references`

**Question.** When a planner produces a design artifact and assigns
implementation, how does the assignee know which file/commit/task is
the source of truth?

**Decision.** A `Task` carries `references: list[TaskRef]` where each
`TaskRef` is `{kind: "file" | "commit" | "task" | "story", value: str,
note: str}`. The planner names them explicitly when emitting
`ASSIGN_TASK`.

**Why.**
- Reviewers already have full visibility into work product via
  `git-changed-files` + `git-diff path=<file>` (commit `41c26d4`).
  The gap isn't visibility — it's the *anchor* the reviewer
  cross-checks against. Without explicit refs, design intent flows
  through positional inference ("the architect's most recent commit
  must be the design") which breaks down once an architect commits
  more than once or commits multiple unrelated things.
- With explicit refs, the reviewer's existing file-by-file process
  has a target: "the task said see `docs/design/auth-flow.md`; does
  the developer's diff reflect the structures named there?" Same
  tools, sharper question.
- Subtasks (D10) naturally reference their parent task's design
  artifact, which makes the propagation chain auditable —
  story → parent task (with file refs) → child task (referencing
  parent) → COMMIT closes:child. The reviewer walks this chain via
  git, not via a new tool.

**Wire shape.** Inside `ASSIGN_TASK`:

```
refs: file:<path> "<note>"
      commit:<hash> "<note>"
      task:<id> "<note>"
      story:<id> "<note>"
```

Renderer surfaces them as a `See:` block under the task on the desk.

**Rejected.** Computing refs automatically from the planner's
upstream commits. Implicit inference is exactly what we're trying to
get away from. Planner names the refs.

---

## Open questions deferred to M4+

These came up during M3.5 design and are intentionally not answered
here:

1. **Task priority / ordering on a desk.** Today: FIFO. M4+ may want
   priority tiers, due dates, or dependency-aware ordering.
2. **Cross-run task carry-over.** Today: tasks die with the run.
   Continuous-app-building (Phase K) may want tasks to persist into
   the next run on the same project.
3. **Persona-level "preferred task" recall.** Today: `Persona.recall`
   doesn't see tasks. Could be a memory layer addition.
4. **Studio task-board view.** Owned by M4+ studio work.
5. **Task estimation / cost forecasting.** Today: tasks have no
   estimated cost. Could feed into the cost estimator.
