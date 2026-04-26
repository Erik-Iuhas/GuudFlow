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
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID

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
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    embeddings_enabled: bool = False

    # Run-level wall-clock deadline (epoch seconds). Set by the runner
    # from a configurable budget. The scheduler checks this between
    # spawn passes and returns SchedulerStatus.DEADLINE_EXCEEDED if hit.
    deadline_at: float | None = None

    # Live subscribers — notified after every post_signal. Excluded from
    # serialisation; populated by the API's WebSocket bridge.
    _subscribers: list[Callable[[Signal], None]] = field(default_factory=list, repr=False)

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
