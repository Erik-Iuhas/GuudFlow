"""When a role's next card continues its opencode session, and when it starts fresh (plan §11.1).

Carrying a whole conversation from card to card made later cards re-read everything that
came before (46% of a session's cost in pacrogue session 3). Starting fresh made workers
re-read the code they had just written. The default now is a fresh session per card plus a
handoff (`core/harness/handoff.py`); a session is kept only in a narrow case where carrying it
is cheaper than starting over:

- the next card continues the last one (the same card, a card that depends on it, or one
  that expects to touch the same files);
- the role was idle for less than the prompt-cache lifetime, so the carried context is still
  cached (a cold re-send costs as much as a fresh start);
- the carried context is small.

Reviewers always start fresh: each review needs the card, its diff and the handoff, not
earlier reviews.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionState:
    """What is known about a role's current session when its next turn starts."""

    has_session: bool
    turns: int                    # turns already run in the session
    idle_seconds: float           # since the last turn in it ended
    context_tokens: int           # context size of its last model call
    continues_card: bool          # the next card continues the last one


@dataclass(frozen=True)
class SessionLimits:
    """Settings for the keep rule."""

    enabled: bool = True
    max_turns: int = 8
    max_idle_seconds: float = 240.0      # below the provider's 5-minute prompt-cache lifetime
    max_context_tokens: int = 60_000


def keep_session(state: SessionState, limits: SessionLimits, *, reviewer: bool) -> tuple[bool, str]:
    """(keep, reason). The reason is recorded with the turn's cost so the policy can be tuned."""
    if not limits.enabled:
        return False, "session reuse off"
    if reviewer:
        return False, "reviewers start fresh"
    if not state.has_session:
        return False, "no session yet"
    if state.turns >= limits.max_turns:
        return False, f"session reached {limits.max_turns} turns"
    if not state.continues_card:
        return False, "next card does not continue the last one"
    if state.idle_seconds > limits.max_idle_seconds:
        return False, f"idle {state.idle_seconds:.0f}s, cache expired"
    if state.context_tokens > limits.max_context_tokens:
        return False, f"context {state.context_tokens} tokens is over {limits.max_context_tokens}"
    return True, "continues the last card"
