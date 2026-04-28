# M3.5 — Design

The full data model, protocol wire format, RBAC integration, and
prompt-layer changes. Read [`decisions.md`](decisions.md) first;
everything below assumes those decisions.

---

## 1. Data model

### 1.1 `Task` dataclass

Lives in `core/workspace.py` next to `StoryState`.

```python
from typing import Literal

TaskStatus = Literal[
    "PENDING",      # on desk, not yet picked up
    "IN_PROGRESS",  # assignee is working on it (latest STARTED w/o COMPLETE)
    "BLOCKED",      # explicitly paused; blocked_on populated
    "COMPLETE",     # closed by a COMMIT that named this task
    "CANCELLED",    # planner withdrew it (rare)
    "SAT_OUT",      # tech_lead granted skip
]

TaskRefKind = Literal["file", "commit", "task", "story"]

@dataclass
class TaskRef:
    kind: TaskRefKind
    value: str            # path, hash, task_id, or story_id
    note: str = ""        # planner's annotation; one short phrase

@dataclass
class Task:
    task_id: str                       # uuid hex; runner-generated
    story_id: str                      # parent story (D5)
    parent_task_id: str | None = None  # D10; depth ≤ 2

    assigned_to: str = ""              # role; D4 — exactly one
    created_by: str = ""               # role of the planner that emitted ASSIGN_TASK
    created_at_iter: int = 0

    goal: str = ""                     # one sentence
    acceptance_criteria: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    references: list[TaskRef] = field(default_factory=list)

    status: TaskStatus = "PENDING"
    started_at_iter: int | None = None
    completed_at_iter: int | None = None
    closed_by_commit: str = ""         # commit hash that fired closes:
    blocked_on: list[str] = field(default_factory=list)  # task_ids
    cancelled_reason: str = ""
```

### 1.2 Workspace plumbing

```python
# Workspace gains:
tasks: dict[str, Task] = field(default_factory=dict)
task_queues: dict[str, list[str]] = field(default_factory=dict)
# task_queues[role] = list of task_ids in FIFO order

def add_task(self, task: Task) -> None: ...
def mutate_task(self, task_id: str, status: TaskStatus, **kwargs) -> None: ...
def desk_for(self, role: str, *, include_complete: bool = False) -> list[Task]: ...
def child_tasks(self, parent_id: str) -> list[Task]: ...
```

Same append-only invariant as `agent_states`: `mutate_task` posts a
signal AND mutates the dict atomically. Subscribers see both.

### 1.3 Persistence

- `data/runs/{run_id}/tasks.jsonl` — append-only mutation log written
  by a new `_task_persister` callback (mirrors `_board_persister`).
- On resume, `core/run_loader.restore_workspace` replays it after the
  board so tasks are in their final state.
- Tasks themselves are reconstructible from the signal trail
  (`TASK_ASSIGNED` carries the full Task payload as JSON in `detail`),
  so the dedicated jsonl is an optimization, not the source of truth.

---

## 2. Protocol wire format

All in `core/protocol.py`. Additive — no existing message changes
shape; `closes:` is a new optional field on `COMMIT`.

### 2.1 `ASSIGN_TASK`

```
ASSIGN_TASK <assignee_role> task:<task_id>
  goal: <one sentence>
  acceptance: <criterion 1> | <criterion 2> | <criterion 3>
  out_of_scope: <thing 1> | <thing 2>
  parent_task: <parent_task_id>
  story: <story_id>
  refs: file:<path> "<note>"
        commit:<hash> "<note>"
        task:<id> "<note>"
        story:<id> "<note>"
```

All fields except the first line are optional. `acceptance` and
`out_of_scope` use `|` as separator (matches existing `VERIFY:` style
in COMMIT).

### 2.2 Lifecycle messages

```
TASK_STARTED task:<task_id> by:<role>
TASK_COMPLETED task:<task_id> commit:<commit_hash>
TASK_BLOCKED task:<task_id> by:<role>
  reason: <one sentence>
  blocked_on: task:<other_id> | task:<another_id>
TASK_CANCELLED task:<task_id> by:<planner_role>
  reason: <one sentence>
```

`TASK_STARTED` is auto-emitted by the runner when an agent's first
turn references a task in any tool call (see §4.2). Agents don't emit
it directly — same pattern as `STORY_OPENED` (the runner auto-tags).

`TASK_COMPLETED` is auto-emitted by the runner when a `COMMIT` with
`closes:` is validated. Agents don't emit it directly.

`TASK_BLOCKED` and `TASK_CANCELLED` are emitted explicitly. Blocked
agents continue working on other desk items.

### 2.3 `COMMIT` extension

Existing format:

```
COMMIT <agent> <commit_hash>
  + <file> [new]
  ~ <file> [mod:N]
VERIFY: <checkpoint> | <checkpoint>
```

New optional trailer line:

```
closes: task:<task_id> | task:<task_id>
```

Existing COMMITs without `closes:` parse exactly as before.
Validation (runner-side, not parser-side):

- Each named `task_id` must be on the committing agent's desk.
- Each must currently be in `IN_PROGRESS` state.
- If validation fails, runner posts a `MANIFEST_INVALIDATED`-style
  warning signal and the COMMIT itself still goes through (we don't
  block work product on validation failure — we surface it).

### 2.4 `SignalType` additions

In `core/signals.py`:

```python
TASK_ASSIGNED = "TASK_ASSIGNED"
TASK_STARTED = "TASK_STARTED"
TASK_COMPLETED = "TASK_COMPLETED"
TASK_BLOCKED = "TASK_BLOCKED"
TASK_CANCELLED = "TASK_CANCELLED"
TASK_OUT_OF_SCOPE = "TASK_OUT_OF_SCOPE"  # runner emits when COMMIT touches files not implied by any open task
```

---

## 3. RBAC integration

### 3.1 New scope

`task:assign` — added to `core/tool_manifest.py::BASELINE_LOCAL_SCOPES`
gating list (not granted by default; explicitly added per archetype).

Granted by default to:

- `agents/builtin/tech_lead.py::TechLead`
- `agents/builtin/architect.py::Architect`
- `agents/builtin/requirements_writer.py::RequirementsWriter`
- `agents/builtin/meta.py::MetaAgent`

Added to each archetype's `default_scopes` constant. The list is
deliberately short — when in doubt, don't grant.

### 3.2 Tool surface

`task:assign` controls one new tool:

```
assign-task
  args:
    assignee: str (role)
    goal: str
    acceptance: list[str]
    out_of_scope: list[str] = []
    parent_task: str | None = None
    story: str | None = None  # defaults to active_story[planner_role]
    references: list[{kind, value, note}] = []
  returns: { task_id }
```

Implemented in `agents/tools/board.py` (next to `BoardMessage`).
Calls `Workspace.add_task` + emits `TASK_ASSIGNED` + emits the
`ASSIGN_TASK` protocol message via the existing `_emit_protocol`
plumbing.

Specialist/Reviewer agents calling `assign-task` raise `ToolDenied`
(same pattern as any RBAC-gated tool).

---

## 4. Runner integration

### 4.1 Desk rendering — `INCOMING WORK` becomes `YOUR DESK + INCOMING`

`core/runner.py::_build_incoming_work` (line 1223 today) is renamed
`_build_iteration_context` and gains a new section.

```
=== YOUR DESK ===

[task:abc123] PENDING — implement JWT issuer
  goal: build an RS256 issuer for short-lived tokens
  acceptance:
    - rs256 signing with key rotation
    - refresh-token rotation on use
    - rate-limit 1000 rpm per principal
  out_of_scope:
    - oauth2 social login
  See:
    file: docs/design/auth-flow.md  (design source)
    commit: def456ab  (architect's design commit)
    story: story_jwt_v1

  └─ subtasks (1):
     [task:abc124] PENDING — verifier module (assigned to: developer)

[task:def456] BLOCKED — refresh-token rotation
  blocked_on: task:abc123
  reason: needs issuer signed token format

=== INCOMING WORK ===
(unchanged from M1 — upstream commits + sat-out info)
=== END ===
```

Rules:

- Empty desk → section omitted entirely (back-compat).
- Subtasks render under their parent indented one level.
- `BLOCKED` and `CANCELLED` tasks render but are visually separated.
- `COMPLETE` tasks omitted (audit trail lives in the board, not the
  prompt).

### 4.2 `TASK_STARTED` auto-emission

Runner inspects each agent's first tool call of an iteration. If the
call's args reference a `task_id` from the agent's desk (e.g. via a
new convention: tools accept an optional `task_id` arg, runner reads
it), emit `TASK_STARTED`. If no explicit `task_id`, the runner
infers the highest-priority `PENDING` task on the desk (FIFO) and
emits `TASK_STARTED` for it.

This keeps the agent prompt simple: agents don't have to declare
"I am working on task X" — the runner figures it out.

### 4.3 `closes:` bookkeeping on COMMIT

The runner-side check is **bookkeeping, not intent verification**.
Verifying that the work matches the design is the reviewer's job —
they have `git-changed-files` and `git-diff path=<file>` (the
file-by-file review tooling from commit `41c26d4`) and can cross-check
the diff against `task.references`. The runner only verifies the
machine-checkable invariants.

In `runner._handle_commit_signal` (existing site that processes
`emit_commit` output):

```python
closed_ids = parse_closes_field(commit_msg)
for tid in closed_ids:
    task = self.workspace.tasks.get(tid)
    if task is None:
        warn("commit closes unknown task", tid)
        continue
    if task.assigned_to != agent.role:
        warn("commit closes task assigned to other role",
             tid, task.assigned_to, agent.role)
        continue
    if task.status != "IN_PROGRESS":
        warn("commit closes task in unexpected state", tid, task.status)
        continue
    self.workspace.mutate_task(tid, "COMPLETE",
                                closed_by_commit=commit.hash)
```

`warn()` posts a `TASK_OUT_OF_SCOPE` signal but does NOT block the
commit — same pattern as today where reviewer judgement is the gate,
not the runner. The reviewer sees the warning signal alongside the
commit and takes it into account, with their normal git diff in hand.

### 4.3b Reviewer flow with tasks (no new tooling needed)

For confidence — what the reviewer actually does after M3.5:

1. `git-changed-files agent/<assignee>` → list of files touched.
2. For each file → `git-diff path=<file>` (capped at ~6000 chars per
   call, file-by-file process from commit `41c26d4`).
3. For each closed task → look up `task.references` and
   `task.acceptance_criteria`.
4. Cross-check: do the touched files line up with referenced files?
   Does the diff produce structures named in the design source? Does
   each acceptance criterion have visible evidence in the diff?
5. Emit `VERIFY_RESULT` per acceptance criterion (PASS/FAIL with
   specific detail), then `APPROVED` or `REJECTED`.

Same tools, no new prompts beyond a one-line addition to the reviewer
system prompt: *"When a closed task is named in the COMMIT, check the
diff against the task's acceptance_criteria and references."*

### 4.4 Subtask cascade

When a child task closes and it has a `parent_task_id`:

```python
parent = workspace.tasks[child.parent_task_id]
siblings = workspace.child_tasks(parent.task_id)
if all(s.status == "COMPLETE" for s in siblings):
    workspace.mutate_task(parent.task_id, "COMPLETE")
```

The parent's `closed_by_commit` is the last child's commit.

### 4.5 User-comment routing — board interface (D2 user flow)

Today: `USER_COMMENT` lands on the board, agents check at step
boundaries via `check_user_board()`.

After M3.5: when a `USER_COMMENT` is posted AND the team has a
planning archetype, the runner identifies the highest-tier on-standby
planner and:

1. Marks the planner's `agent_state` as `READY` (if not already
   `RUNNING`).
2. Adds an internal "interface" task to the planner's desk with
   `goal: respond to user comment` + `references: [story:active,
   commit:latest]`. This task has a synthetic `task_id` prefix
   (`comment_<comment_id>`) so it's distinguishable.
3. The planner's next iteration includes the comment text + their new
   desk entry. Their planner-archetype prompt instructs them to
   either answer the user, decompose into tasks for downstream
   agents, or post a HUMAN_NEEDED if they can't decide.

If no planning archetype is in the team → fall back to current
behaviour (agents check_user_board at step boundaries). Empty-desk
projects unaffected.

Priority for "highest-tier on-standby":

1. `tech_lead` (if hired and not currently RUNNING)
2. `architect`
3. `requirements_writer`
4. `meta`
5. (none) → fallback

---

## 5. Prompt layer impact

### 5.1 No new system-prompt layer

Tasks render in the user-turn message (the volatile suffix), not the
system prompt. Reasons:

- Tasks change every iteration; putting them in the system prompt
  invalidates the Anthropic prompt cache.
- The 12-layer system prompt stack is already crowded — see
  `docs/diagrams/11-prompt-stack.md`. Don't add a 13th layer for
  state that's better in the user message.

### 5.2 Planner archetype prompt addition

A new line is appended to each planner archetype's `system_prompt`:

```
TASK ASSIGNMENT: When you produce work that another agent should
implement, decompose it into one or more tasks via the assign-task
tool. Always populate acceptance criteria. Reference your own
artifacts (files, commits, parent stories) so the assignee has the
source of truth. Do not assign work outside the team's roster.
```

This is ~50 tokens per planner per iteration. Within the cacheable
prefix.

### 5.3 Specialist prompt addition (non-reviewer)

Conditional on the agent having a non-empty desk on first iteration:

```
DESK DISCIPLINE: Your desk lists explicit tasks with acceptance
criteria. Work the desk top-to-bottom. Close each task with a
COMMIT that names it via "closes: task:<id>". If a criterion is
unclear, emit ASK to the task's created_by role before working on it.
```

Injected once at the start of the user-turn message when desk is
non-empty. ~40 tokens; not cached (volatile).

### 5.4 Reviewer prompt addition

Reviewer archetypes get one extra line in their static system prompt
(cacheable):

```
TASK-AWARE REVIEW: When a COMMIT names "closes: task:<id>", look up
that task's acceptance_criteria and references. Cross-check the
git-diff against the references; verify each acceptance criterion is
satisfied; emit VERIFY_RESULT per criterion. The runner posts
TASK_OUT_OF_SCOPE signals when a commit closes a task it shouldn't —
treat those as evidence, not as final verdicts.
```

~50 tokens once, in the cacheable prefix. No volatile injection
needed for reviewers — git diff + the task on the closing agent's
desk is what they read.

---

## 6. API surface

### 6.1 `GET /api/runs/{run_id}/desks`

Returns:

```json
{
  "desks": {
    "developer": [
      { "task_id": "...", "status": "...", "goal": "...", "..." }
    ],
    "code_reviewer": []
  },
  "tasks_by_id": { "task_id": Task, ... }
}
```

Bearer-auth gated. Read-only.

### 6.2 WebSocket event

`/ws/runs/{run_id}` already streams signals. Task lifecycle signals
(`TASK_*`) are already streamed by virtue of being signals. No new
WS handler.

Studio M4+ owns rendering this.

---

## 7. Test surface

Each chunk lands its own tests (see [`chunks.md`](chunks.md)). Whole-
feature integration tests:

- `tests/integration/test_task_handoff_m3_5.py`
  - architect assigns → developer closes via COMMIT
  - parent task auto-closes when last child closes
  - reviewer checks against acceptance_criteria, not VERIFY:
  - planner without `task:assign` scope is denied
  - empty desk reproduces M1 INCOMING WORK exactly
  - resume after crash: tasks restored from board.jsonl
  - user_comment → planner pickup → assigns → developer works
  - subtask depth cap (3rd level rejected with clear error)

---

## 8. What this doesn't change

Listed for confidence:

- `Scheduler` — untouched.
- `Workspace.board` append-only invariant — preserved.
- `agents/registry.py` — no new agents registered.
- DESIGN.md sections 1–37 — no edits, addendum §38.12 added.
- Existing protocol messages — `COMMIT` adds optional field; everything else identical.
- LLM call shape — no new `cache_system` semantics, no new tool-use shape.
- Tool naming convention — `task:assign` scope, `assign-task` tool name (hyphenated, matches §38.7).

If M4+ wants to revisit any of this, the only deletion path is
removing the four task-lifecycle signal types, the `closes:` field on
COMMIT, and the desk render block. Roughly 200 LOC total. Reversible.
