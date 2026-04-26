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

    PROTOCOL = "PROTOCOL"


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
