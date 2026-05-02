"""Workspace — the central shared state for a run.

Every agent reads from and writes to this object. Nothing else holds state.
The board is append-only: signals are added, never mutated or removed.
The only permitted in-place change is `Signal.seen_by` via `mark_seen()`.

See DESIGN.md §3.1, §17, §19, §28.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal
from uuid import UUID, uuid4

from core.signals import Signal, SignalType

if TYPE_CHECKING:
    from core.file_workspace import FileWorkspace


class AgentState(str, Enum):
    """Lifecycle state of an agent role within a single run."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    WAITING_FOR_HUMAN = "waiting_for_human"
    # M1 — handshake protocol
    WAITING_FOR_PEER = "waiting_for_peer"          # blocked on ASK/ANSWER
    WAITING_FOR_SKIP_CONFIRM = "waiting_for_skip_confirm"  # REQUEST_SKIP pending
    SAT_OUT = "sat_out"                            # skip confirmed; counts as complete


# ── Typed sub-records ───────────────────────────────────────────────────────


@dataclass
class Artifact:
    """A typed agent output. Never use `dict[str, str]` — DESIGN §15.

    `content` is a string for text/code artifacts and a dict for structured
    artifacts (uicanvas, api_spec, data_model). Adding a new artifact type
    requires only a new `type` value and a renderer; no core change.
    """

    agent_role: str
    type: str
    content: str | dict[str, Any]
    iteration: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class HumanRequest:
    """Hard-pause request — set on workspace.pending_human when an agent
    calls ask_human(). The scheduler halts new spawns while this is set."""

    from_agent: str
    question: str
    context: str | None = None
    iteration: int = 0


@dataclass
class PendingScreenshot:
    from_agent: str
    file_path: str
    viewport: str
    awaiting_feedback: bool = True


@dataclass
class CommitRecord:
    hash: str
    branch: str
    agent: str
    message: str
    iteration: int
    files: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class IterationMetrics:
    """One row per completed agent iteration (one full BaseAgent.run() invocation).

    Multiple LLM calls within one run() are summed into the *_tokens fields.
    """

    run_id: str
    role: str
    iteration: int
    model: str = ""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    llm_calls: int = 0

    # token count of the final assistant message (useful for "did the agent ramble?")
    final_message_tokens: int = 0

    started_at: float = 0.0
    ended_at: float = 0.0
    elapsed_seconds: float = 0.0

    recall_episodes_count: int = 0
    recall_patterns_count: int = 0
    repo_map_tokens: int = 0
    relevant_files_tokens: int = 0
    git_diff_tokens: int = 0
    context_tokens_total: int = 0
    # Track 3d.2: tokens from direct fs-read / fs-list / fs-read_range calls
    # that bypassed get_context(). Separate from context_tokens_total so the
    # row tells the full story when agents skip the context manager.
    direct_file_read_tokens: int = 0

    tools_called: list[str] = field(default_factory=list)
    # Track 1b: per-tool token contribution. Keys are tool names, values are
    # the total token cost of all results returned by that tool this
    # iteration. Read-class tool entries also feed direct_file_read_tokens.
    tool_token_breakdown: dict[str, int] = field(default_factory=dict)
    # Track 3c: tool output compression savings. Counts measure the JSON-
    # serialised forms of tool results before and after compression — what
    # the LLM actually receives. Compression happens at BaseAgent.call_tool
    # via agents/tools/output_compress.py.
    tool_output_tokens_raw: int = 0
    tool_output_tokens_compressed: int = 0
    tokens_saved_by_compression: int = 0
    # Track 12: per-use-case sub-LLM dispatch roll-up. Keyed by use_case
    # name (e.g. "summarise-subtask", "protocol-cleanup", "llm-dispatch"),
    # each entry is `{"calls": int, "input_tokens": int, "output_tokens": int}`.
    # Lets the user see where their sub-dispatch budget went per run.
    sub_llm_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)

    completed: bool = False
    failed: bool = False
    no_op: bool = False


@dataclass
class ContextSnapshot:
    """One row per get_context() call. Records what was injected, not why."""

    run_id: str
    role: str
    iteration: int
    task: str

    tier: str = "solid"
    max_tokens: int = 0
    used_tokens: int = 0

    repo_map_tokens: int = 0
    git_diff_tokens: int = 0
    relevant_files: list[tuple[str, int]] = field(default_factory=list)
    recent_signals_count: int = 0

    recalled_episodes: list[str] = field(default_factory=list)
    recalled_patterns: list[str] = field(default_factory=list)

    timestamp: float = 0.0


@dataclass
class StoryState:
    """A live story (M1 §7b). Tracks open/closed status + which agents own it.

    `active_for` is the set of roles for which this story is the currently
    active story (auto-resolved by the runner). `parent` is the parent story_id
    for sub-stories (one level deep only).
    """

    story_id: str
    name: str
    goal: str
    owner_role: str | None = None
    parent_story_id: str | None = None
    opened_at_iteration: int = 0
    base_ref: str | None = None  # git SHA at story-open; baseline for story-scoped diffs
    closed: bool = False
    closed_outcome: str | None = None  # "shipped" | "abandoned" | "deferred"
    closed_summary: str = ""


# ── Per-agent task desks (DESIGN §38.12) ────────────────────────────────────


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
    """One named source-of-truth pointer attached to a Task. Reviewers
    cross-check the assignee's diff against these refs (DESIGN §38.12)."""

    kind: TaskRefKind
    value: str
    note: str = ""


@dataclass
class Task:
    """One work item on an agent's desk. Issued by a planning archetype
    via the `assign-task` tool. Closed at COMMIT time when the assignee
    emits `closes: task:<task_id>`. See DESIGN §38.12."""

    task_id: str
    story_id: str
    parent_task_id: str | None = None

    assigned_to: str = ""
    created_by: str = ""
    created_at_iter: int = 0

    goal: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    references: list[TaskRef] = field(default_factory=list)

    status: TaskStatus = "PENDING"
    started_at_iter: int | None = None
    completed_at_iter: int | None = None
    closed_by_commit: str = ""
    blocked_on: list[str] = field(default_factory=list)
    cancelled_reason: str = ""

    # M5.5: set by TaskSummarizer on close; persisted via tasks.jsonl mutation.
    completion_summary: dict | None = None


# Map a TaskStatus transition to the SignalType the runner posts when
# `mutate_task` fires. PENDING is the initial state and never re-enters
# (callers must use `add_task` for the assignment).
_STATUS_SIGNAL: dict[str, SignalType] = {
    "IN_PROGRESS": SignalType.TASK_STARTED,
    "COMPLETE": SignalType.TASK_COMPLETED,
    "BLOCKED": SignalType.TASK_BLOCKED,
    "CANCELLED": SignalType.TASK_CANCELLED,
    "SAT_OUT": SignalType.TASK_CANCELLED,
}


@dataclass
class GitState:
    """Runtime view of the git repo inside `workspace/working/`.

    `agent_branches` maps role → branch name (e.g. {"developer": "agent/developer"}).
    Populated by GitWorkspace as it creates branches; runner uses it for merges.
    """

    repo_path: str = "working/"
    main_branch: str = "main"
    agent_branches: dict[str, str] = field(default_factory=dict)
    commits: list[CommitRecord] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)


@dataclass
class PackageRequest:
    from_agent: str
    package: str
    reason: str
    iteration: int


@dataclass
class ApprovalRequest:
    """Runner-initiated cost-control gate (Phase C.0.5).

    The runner posts an APPROVAL_NEEDED signal and parks the gated
    operation until the user POSTs `/api/runs/{id}/approval/{id}` with
    `grant` or `deny`. Distinct from `HumanRequest`: that's an
    in-character agent question; this is the system asking permission
    to spend tokens.

    `kind` is a stable string identifier used by the runner to decide
    *what* to do on grant — e.g. `"heavy_tier_dispatch"` resumes the
    side-task spawn that was paused.
    """

    id: str
    kind: str  # "heavy_tier_dispatch" | "iteration_breach" | "pre_run_cost" | ...
    summary: str
    estimated_cost_usd: float | None = None
    context: dict[str, Any] = field(default_factory=dict)
    triggered_by: str | None = None  # role that caused the gate, if any
    decision: str | None = None  # None | "granted" | "denied"
    decision_note: str | None = None
    iteration: int = 0
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None


@dataclass
class RepoMapEntry:
    path: str
    type: str
    symbols: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    rank: float = 0.0


@dataclass
class RepoMap:
    """Compact ranked codebase representation. Populated by core/repo_map.py
    and rebuilt incrementally on GIT_COMMIT."""

    generated_at: str = ""
    entries: list[RepoMapEntry] = field(default_factory=list)


# Signal types that should appear on user_board (the filtered view agents
# call check_user_board() against). HUMAN_RESPONDED is user-originated but
# is a control signal, not ambient feedback — it is NOT mirrored here.
USER_BOARD_TYPES: frozenset[SignalType] = frozenset({
    SignalType.USER_COMMENT,
    SignalType.USER_COMMENT_PRIORITY,
    SignalType.USER_VISUAL_FEEDBACK,
})


# ── The workspace itself ────────────────────────────────────────────────────


@dataclass
class Workspace:
    run_id: str
    goal: str
    phase: str = "init"
    max_iterations: int = 10

    board: list[Signal] = field(default_factory=list)
    user_board: list[Signal] = field(default_factory=list)
    artifacts: dict[str, Artifact] = field(default_factory=dict)

    # FileWorkspace is provisioned at run start by the runner. None during
    # construction so unit tests of board logic don't need a real filesystem.
    files: "FileWorkspace | None" = None

    agent_states: dict[str, AgentState] = field(default_factory=dict)
    hired_personas: dict[str, str] = field(default_factory=dict)
    pending_human: HumanRequest | None = None
    pending_screenshots: list[PendingScreenshot] = field(default_factory=list)
    git: GitState = field(default_factory=GitState)
    pending_package_requests: list[PackageRequest] = field(default_factory=list)
    pending_approvals: list[ApprovalRequest] = field(default_factory=list)

    iteration: int = 0
    completed_agents: list[str] = field(default_factory=list)
    repo_map: RepoMap = field(default_factory=RepoMap)

    # M1 §7b — stories. `stories` is the index by story_id; `active_story`
    # maps role → story_id (the per-agent active-story pointer the runner
    # uses to auto-tag emitted protocol messages).
    stories: dict[str, "StoryState"] = field(default_factory=dict)
    active_story: dict[str, str] = field(default_factory=dict)
    embeddings_enabled: bool = False

    # M3.5 — per-agent task desks (DESIGN §38.12). `tasks` is the index by
    # task_id; `task_queues[role]` is the FIFO desk for `role`. Same
    # append-only invariant as `agent_states`: every transition posts a
    # signal AND mutates the dict atomically.
    tasks: dict[str, Task] = field(default_factory=dict)
    task_queues: dict[str, list[str]] = field(default_factory=dict)
    # Track 7 — currently-active subtask per role. Set by `mutate_task`
    # on IN_PROGRESS transitions, cleared on terminal status. Read by
    # `effective_system_prompt` Layer 4.7 to surface the agent's current
    # focus and by Track 8 (subtask context hot-swap) to bound the
    # message-history span at closure time.
    active_subtask: dict[str, str] = field(default_factory=dict)

    # Run-level wall-clock deadline (epoch seconds). Set by the runner
    # from a configurable budget. The scheduler checks this between
    # spawn passes and returns SchedulerStatus.DEADLINE_EXCEEDED if hit.
    deadline_at: float | None = None

    # Live subscribers — notified after every post_signal. Excluded from
    # serialisation; populated by the API's WebSocket bridge.
    _subscribers: list[Callable[[Signal], None]] = field(default_factory=list, repr=False)

    # Board persister — fires after every post_signal so a crashed run
    # leaves a complete signal trail on disk for resume (Phase G-prime.0).
    # The runner attaches one when given a run_root; tests / synthetic
    # workspaces leave it None and behave as before. Persister exceptions
    # are swallowed for the same reason subscriber exceptions are: a
    # broken persister can't be allowed to break the workspace.
    _board_persister: Callable[[Signal], None] | None = field(default=None, repr=False)
    # Artifact persister (Phase G-prime.1) — fires whenever set_artifact
    # mutates the artifacts dict so restored workspaces have agent
    # outputs available without re-running completed agents.
    _artifact_persister: Callable[[dict[str, "Artifact"]], None] | None = field(
        default=None, repr=False,
    )
    # Task persister (M3.5) — fires after every add_task / mutate_task so a
    # crashed run leaves a complete task mutation log on disk for resume.
    # Mirrors `_board_persister`. Callback receives the mutated Task.
    _task_persister: Callable[["Task"], None] | None = field(default=None, repr=False)
    # Iteration metrics persister (Track 1) — fires after each completed
    # BaseAgent.run() invocation with an IterationMetrics row.
    _iteration_metrics_persister: Callable[["IterationMetrics"], None] | None = field(
        default=None, repr=False,
    )
    # Context log persister (Track 1) — fires after each get_context() call
    # with a ContextSnapshot row.
    _context_log_persister: Callable[["ContextSnapshot"], None] | None = field(
        default=None, repr=False,
    )

    # In-memory log lists (always populated; persister is optional).
    iteration_metrics: list["IterationMetrics"] = field(default_factory=list)
    context_log: list["ContextSnapshot"] = field(default_factory=list)

    # ── Board management ────────────────────────────────────────────────────

    def post_signal(self, signal: Signal) -> None:
        """Append a signal to the board. User-comment-type signals are
        mirrored to `user_board` so agents can scan the filtered view via
        check_user_board().

        Append-only — callers must never mutate or remove board entries.
        The single permitted post-hoc change is `mark_seen()`.

        Subscribers (added via `subscribe()`) are notified after the
        board append. Subscriber exceptions are swallowed so a buggy
        subscriber can't break the workspace.
        """
        self.board.append(signal)
        if signal.type in USER_BOARD_TYPES:
            self.user_board.append(signal)
        for sub in list(self._subscribers):
            try:
                sub(signal)
            except Exception:
                continue
        if self._board_persister is not None:
            try:
                self._board_persister(signal)
            except Exception:
                # A broken persister (disk full, permissions) must not
                # take the run down with it. The end-of-run consolidated
                # board.json write in `_persist_run` is the safety net.
                pass

    def set_board_persister(self, callback: Callable[[Signal], None] | None) -> None:
        """Install (or clear) the persister called after every signal.
        Distinct from `subscribe()` — only one persister at a time, and
        it has a single durable purpose (write to disk for resume)."""
        self._board_persister = callback

    def set_artifact_persister(
        self, callback: Callable[[dict[str, Artifact]], None] | None,
    ) -> None:
        """Install (or clear) the artifact persister. Fires after each
        `set_artifact()` call with the full current artifacts dict."""
        self._artifact_persister = callback

    def set_artifact(self, role: str, artifact: Artifact) -> None:
        """Record an agent's final output artifact. Call this instead of
        mutating `self.artifacts` directly so the artifact persister
        (Phase G-prime.1) sees every change and resumed runs can read
        the artifacts of completed agents without re-running them."""
        self.artifacts[role] = artifact
        if self._artifact_persister is not None:
            try:
                self._artifact_persister(self.artifacts)
            except Exception:
                # Same swallow contract as the board persister.
                pass

    def subscribe(self, callback: Callable[[Signal], None]) -> Callable[[], None]:
        """Register a callback for new signals. Returns an unsubscribe
        function. Used by the API's WebSocket bridge to stream signals
        live without polling."""
        self._subscribers.append(callback)
        def _unsubscribe():
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass
        return _unsubscribe

    def mark_seen(self, signal_id: UUID, role: str) -> None:
        """Record that an agent role has absorbed a signal. Idempotent."""
        for sig in self.board:
            if sig.id == signal_id:
                sig.mark_seen_by(role)
                return
        raise KeyError(f"signal {signal_id} not present on board")

    def unseen_user_comments(self, role: str) -> list[Signal]:
        """Return user_board signals this role has not yet absorbed.
        Agents call this from check_user_board()."""
        return [s for s in self.user_board if role not in s.seen_by]

    def find_pending_approval(self, approval_id: str) -> "ApprovalRequest | None":
        """Look up a pending approval by id. Returns None if there's
        no match or the approval has already been decided."""
        for req in self.pending_approvals:
            if req.id == approval_id and req.decision is None:
                return req
        return None

    def record_approval(
        self,
        approval_id: str,
        decision: str,
        note: str | None = None,
    ) -> "ApprovalRequest | None":
        """Mark a pending approval as decided. Returns the updated
        ApprovalRequest or None if not found / already decided.
        Caller is responsible for posting the matching APPROVAL_GRANTED
        / APPROVAL_DENIED signal afterwards (so subscribers fire after
        state is set)."""
        req = self.find_pending_approval(approval_id)
        if req is None:
            return None
        if decision not in ("granted", "denied"):
            raise ValueError(
                f"decision must be 'granted' or 'denied', got {decision!r}"
            )
        req.decision = decision
        req.decision_note = note
        req.decided_at = datetime.now(timezone.utc)
        return req

    def signals_of_type(self, *types: SignalType) -> list[Signal]:
        """Read-only filter. Useful for the scheduler scanning for COMPLETE,
        and for read_protocol() filtering PROTOCOL signals."""
        type_set = set(types)
        return [s for s in self.board if s.type in type_set]

    # ── Task desks (M3.5, DESIGN §38.12) ────────────────────────────────────

    def set_task_persister(self, callback: Callable[["Task"], None] | None) -> None:
        """Install (or clear) the task persister. Fires after every
        `add_task` / `mutate_task` with the just-mutated Task."""
        self._task_persister = callback

    def add_task(self, task: Task) -> None:
        """Place a new task on its assignee's desk. Posts TASK_ASSIGNED
        AND appends to `task_queues[assigned_to]` AND inserts into
        `tasks` atomically. Generates a fresh `task_id` if blank."""
        if not task.assigned_to:
            raise ValueError("Task.assigned_to is required")
        if not task.task_id:
            task.task_id = uuid4().hex
        if task.task_id in self.tasks:
            raise ValueError(f"task_id {task.task_id!r} already exists")
        if task.parent_task_id is not None:
            parent = self.tasks.get(task.parent_task_id)
            if parent is None:
                raise ValueError(f"unknown parent_task_id {task.parent_task_id!r}")
            # Track 7 — depth cap lifted 2 → 3 to make room for the
            # synthetic root layer:
            #   depth 1: root:{role}        (one per agent per run)
            #   depth 2: decomposed subtasks (decompose-task / assign-task subtasks)
            #   depth 3: further decomposition by the receiver
            grandparent = (
                self.tasks.get(parent.parent_task_id)
                if parent.parent_task_id else None
            )
            if grandparent is not None and grandparent.parent_task_id is not None:
                raise ValueError(
                    "subtask depth cap (3 levels): cannot nest deeper than "
                    "root → subtask → sub-subtask"
                )

        self.tasks[task.task_id] = task
        self.task_queues.setdefault(task.assigned_to, []).append(task.task_id)

        self.post_signal(Signal(
            type=SignalType.TASK_ASSIGNED,
            summary=f"task:{task.task_id} → {task.assigned_to}: {task.goal[:80]}",
            from_agent=task.created_by or None,
            iteration=task.created_at_iter,
            detail=task.task_id,
        ))
        if self._task_persister is not None:
            try:
                self._task_persister(task)
            except Exception:
                pass

    def mutate_task(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        iteration: int | None = None,
        **kwargs: Any,
    ) -> Task:
        """Transition `task_id` to `status` and post the matching TASK_*
        signal atomically. Extra kwargs are applied to the Task before
        the signal fires (e.g. `closed_by_commit`, `blocked_on`,
        `cancelled_reason`). Returns the mutated Task."""
        task = self.tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown task_id {task_id!r}")
        if status == "PENDING":
            raise ValueError("PENDING is the initial state; use add_task")

        task.status = status
        for key, value in kwargs.items():
            if not hasattr(task, key):
                raise ValueError(f"Task has no attribute {key!r}")
            setattr(task, key, value)
        if status == "IN_PROGRESS" and task.started_at_iter is None and iteration is not None:
            task.started_at_iter = iteration
        if status == "COMPLETE" and iteration is not None:
            task.completed_at_iter = iteration

        # Track 7 — keep `active_subtask[role]` in sync with reality.
        # Set on START; clear on any terminal transition. Layer 4.7 reads
        # this to surface the agent's current focus; Track 8 reads it to
        # bound the message-history span at closure time.
        if task.assigned_to:
            if status == "IN_PROGRESS":
                self.active_subtask[task.assigned_to] = task.task_id
            elif status in ("COMPLETE", "BLOCKED", "CANCELLED", "SAT_OUT"):
                if self.active_subtask.get(task.assigned_to) == task.task_id:
                    self.active_subtask.pop(task.assigned_to, None)

        signal_type = _STATUS_SIGNAL[status]
        detail = task.task_id
        if status == "COMPLETE" and task.closed_by_commit:
            detail = f"{task.task_id};commit={task.closed_by_commit}"
        self.post_signal(Signal(
            type=signal_type,
            summary=f"task:{task.task_id} → {status}",
            from_agent=task.assigned_to or None,
            iteration=iteration if iteration is not None else self.iteration,
            detail=detail,
        ))
        if self._task_persister is not None:
            try:
                self._task_persister(task)
            except Exception:
                pass
        return task

    def desk_for(
        self,
        role: str,
        *,
        include_complete: bool = False,
        include_synthetic_roots: bool = False,
    ) -> list[Task]:
        """Return tasks currently on `role`'s desk in FIFO order.
        COMPLETE and CANCELLED are filtered by default.

        Track 7 — synthetic root tasks (`task_id` prefix `root:`) are
        plumbing for `decompose-task`'s default parent. They're filtered
        by default so they don't pollute the Kanban or trip M3.5's
        "synthetic task when desk empty" check. Pass
        `include_synthetic_roots=True` to see them (the runner's
        decompose-task resolution path does this).
        """
        ids = self.task_queues.get(role, [])
        out: list[Task] = []
        for tid in ids:
            t = self.tasks.get(tid)
            if t is None:
                continue
            if not include_complete and t.status in ("COMPLETE", "CANCELLED", "SAT_OUT"):
                continue
            if not include_synthetic_roots and tid.startswith("root:"):
                continue
            out.append(t)
        return out

    def child_tasks(self, parent_id: str) -> list[Task]:
        """Return immediate children of `parent_id` in FIFO order
        (creation order — `tasks` is insertion-ordered)."""
        return [
            t for t in self.tasks.values()
            if t.parent_task_id == parent_id
        ]

    # ── Track 1: iteration metrics + context log ────────────────────────────

    def add_iteration_metrics(self, m: "IterationMetrics") -> None:
        """Record one completed agent iteration. Fires the persister if set."""
        self.iteration_metrics.append(m)
        persister = self._iteration_metrics_persister
        if persister is not None:
            try:
                persister(m)
            except Exception:
                pass

    def record_context_snapshot(self, s: "ContextSnapshot") -> None:
        """Record one get_context() call. Fires the persister if set."""
        self.context_log.append(s)
        persister = self._context_log_persister
        if persister is not None:
            try:
                persister(s)
            except Exception:
                pass
