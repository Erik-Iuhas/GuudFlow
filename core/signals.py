"""Signal types and Signal dataclass — the language agents use to communicate.

Signals are informative, never imperative. An agent posts a signal and moves on;
it never directly tells another agent what to do. The board is append-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4


class SignalType(str, Enum):
    """All signal types posted to the workspace board.

    Lifecycle types describe agent state transitions. PROTOCOL carries the
    structured inter-agent messages defined in core/protocol.py. Other types
    cover human interaction, git, package management, and visual feedback.
    """

    STARTED = "STARTED"
    THINKING = "THINKING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"

    HUMAN_NEEDED = "HUMAN_NEEDED"
    HUMAN_RESPONDED = "HUMAN_RESPONDED"

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    # Auto-escalation (DESIGN §23) — the runner posts this when an
    # agent's REJECTED count crosses the project's threshold; tech_lead
    # wakes on it and posts a focused ACTION recommendation back.
    ESCALATION = "ESCALATION"

    # Approval gates (Phase C.0.5) — runner-initiated cost-control
    # gates. Distinct from HUMAN_NEEDED (in-character agent questions).
    # The runner posts APPROVAL_NEEDED before a gated event (HEAVY-tier
    # dispatch, iteration breach, run start with cost preview), pauses,
    # and resumes when the user POSTs APPROVAL_GRANTED or APPROVAL_DENIED.
    APPROVAL_NEEDED = "APPROVAL_NEEDED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"

    USER_COMMENT = "USER_COMMENT"
    USER_COMMENT_PRIORITY = "USER_COMMENT_PRIORITY"

    SCREENSHOT_READY = "SCREENSHOT_READY"
    USER_VISUAL_FEEDBACK = "USER_VISUAL_FEEDBACK"

    GIT_COMMIT = "GIT_COMMIT"
    GIT_MERGE = "GIT_MERGE"
    GIT_CONFLICT = "GIT_CONFLICT"

    PACKAGE_REQUEST = "PACKAGE_REQUEST"
    PACKAGE_APPROVED = "PACKAGE_APPROVED"
    PACKAGE_REJECTED = "PACKAGE_REJECTED"

    # Credentials broker (DESIGN §30) — RBAC-mediated access to credentialed
    # services (MCPs, external APIs). Agents never hold credentials; they
    # request the broker to make the call and receive only the result.
    CREDENTIAL_REQUEST = "CREDENTIAL_REQUEST"
    CREDENTIAL_GRANTED = "CREDENTIAL_GRANTED"
    CREDENTIAL_DENIED = "CREDENTIAL_DENIED"

    # File coordination (DESIGN §32) — advisory effort-coordination for
    # parallel agents editing the same path. Branch-per-agent (§19) handles
    # data safety; these signals communicate runtime intent.
    FILE_CLAIM = "FILE_CLAIM"
    FILE_RELEASE = "FILE_RELEASE"

    # Tool surface change (DESIGN §31) — broker emits this when an agent's
    # manifest needs rebuilding (e.g. mid-run scope grant, MCP catalog
    # change). Agents pick up the new manifest at the next step boundary.
    MANIFEST_INVALIDATED = "MANIFEST_INVALIDATED"

    # M3.5 — per-agent task desks (DESIGN §38.12). Tasks are intra-role
    # work items posted by planning archetypes via the assign-task tool.
    # Closure happens at COMMIT time via a `closes: task:<id>` trailer;
    # the runner posts TASK_OUT_OF_SCOPE when a commit closes a task it
    # shouldn't (wrong assignee, wrong state, unknown id).
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_OUT_OF_SCOPE = "TASK_OUT_OF_SCOPE"

    # Training Camp (DESIGN §35, Phase F.1) — rubric_scorer posts
    # RUBRIC_SCORE at end-of-training-run with the weighted score and
    # per-check breakdown. Tournament launcher aggregates per-run scores
    # and posts TOURNAMENT_RESULT once all participants finish.
    RUBRIC_SCORE = "RUBRIC_SCORE"
    TOURNAMENT_RESULT = "TOURNAMENT_RESULT"

    PROTOCOL = "PROTOCOL"

    # Protocol parse failure — posted by the runner each time an agent's
    # response lacks a valid trailing protocol block. Visible in the Run view
    # so users can see retry progress before the budget is exhausted.
    PROTOCOL_PARSE_FAIL = "PROTOCOL_PARSE_FAIL"

    # Agent-to-agent messaging (non-blocking). An agent posts AGENT_MESSAGE
    # directed at another role; the recipient reads it at the start of their
    # next turn. Replies are also AGENT_MESSAGE signals.
    AGENT_MESSAGE = "AGENT_MESSAGE"

    # M1 — handshake protocol timeouts
    PEER_TIMEOUT = "PEER_TIMEOUT"       # ASK target didn't answer within deadline
    SKIP_TIMEOUT = "SKIP_TIMEOUT"       # REQUEST_SKIP confirmer didn't respond in time

    # Track 4.2 — pr_mode=manual. Posted at end-of-run when leaf work has been
    # merged into an integration branch instead of main. Carries the branch
    # name and a paste-ready `gh pr create` command.
    PR_READY = "PR_READY"

    # Track 9 — sidecar rescued a parse-fail. Posted when the agent emitted
    # prose without a valid trailing protocol block AND the runner-side
    # `protocol-cleanup` dispatch returned a parseable replacement, so the
    # heavy retry was skipped. Detail names the recovered block kind.
    PROTOCOL_CLEANUP_AUTO = "PROTOCOL_CLEANUP_AUTO"

    # opencode harness (docs/OPENCODE_HARNESS_PLAN.md).
    # What an agent was allowed to do for this spawn: the effective capability
    # block plus a digest of the compiled permission rules (plan §5.7).
    CAPABILITIES = "CAPABILITIES"
    # The validated structured result an agent submitted for a turn (plan §4.2).
    AGENT_RESULT = "AGENT_RESULT"
    # One finished opencode tool call (completed or error), from the event bridge.
    TOOL_CALL = "TOOL_CALL"
    # The agent's own todo list changed (opencode todowrite).
    AGENT_TODO = "AGENT_TODO"
    # A host-side web_search / web_fetch call (plan §5.8).
    WEB_REQUEST = "WEB_REQUEST"
    # A visual or document artifact was produced (plan §5.10).
    ARTIFACT = "ARTIFACT"
    # The overseer changed course (plan §11.3).
    OVERSEER_INTERVENTION = "OVERSEER_INTERVENTION"


Priority = Literal["normal", "high"]


# Signals that originate from the user, not from any agent.
USER_SIGNAL_TYPES: frozenset[SignalType] = frozenset({
    SignalType.USER_COMMENT,
    SignalType.USER_COMMENT_PRIORITY,
    SignalType.USER_VISUAL_FEEDBACK,
    SignalType.HUMAN_RESPONDED,
})


@dataclass
class Signal:
    """A single entry on the workspace board.

    Signals are append-only — never mutated, never deleted. The `seen_by` list
    is the single permitted form of in-place update, used to track which agents
    have absorbed a USER_COMMENT.
    """

    type: SignalType
    summary: str
    from_agent: str | None = None
    from_user: bool = False
    priority: Priority = "normal"
    detail: str | None = None
    iteration: int = 0
    seen_by: list[str] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.type in USER_SIGNAL_TYPES:
            self.from_user = True
            if self.from_agent is not None:
                raise ValueError(
                    f"Signal type {self.type} is user-originated; from_agent must be None"
                )
        else:
            if self.from_user:
                raise ValueError(
                    f"Signal type {self.type} is agent-originated; from_user must be False"
                )

        if self.priority not in ("normal", "high"):
            raise ValueError(f"priority must be 'normal' or 'high', got {self.priority!r}")

    def mark_seen_by(self, role: str) -> None:
        """Record that an agent has absorbed this signal.

        Idempotent — calling twice with the same role does nothing.
        """
        if role not in self.seen_by:
            self.seen_by.append(role)
