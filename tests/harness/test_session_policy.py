"""Tests for core.harness.session_policy: when a card keeps its session (plan §11.1)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.harness.session_policy import SessionLimits, SessionState, keep_session

WARM = SessionState(has_session=True, turns=1, idle_seconds=30, context_tokens=20_000,
                    continues_card=True)
LIMITS = SessionLimits()


def test_keeps_a_warm_small_session_that_continues_the_card() -> None:
    assert keep_session(WARM, LIMITS, reviewer=False) == (True, "continues the last card")


@pytest.mark.parametrize(("change", "reason"), [
    ({"has_session": False}, "no session yet"),
    ({"turns": 8}, "session reached 8 turns"),
    ({"continues_card": False}, "next card does not continue the last one"),
    ({"idle_seconds": 400}, "idle 400s, cache expired"),
    ({"context_tokens": 90_000}, "context 90000 tokens is over 60000"),
])
def test_starts_fresh(change: dict[str, object], reason: str) -> None:
    assert keep_session(replace(WARM, **change), LIMITS, reviewer=False) == (False, reason)


def test_reviewers_always_start_fresh() -> None:
    assert keep_session(WARM, LIMITS, reviewer=True) == (False, "reviewers start fresh")


def test_reuse_switched_off() -> None:
    assert keep_session(WARM, SessionLimits(enabled=False), reviewer=False) == (
        False, "session reuse off")
