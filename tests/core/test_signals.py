"""Tests for core/signals.py."""

from __future__ import annotations

import pytest

from core.signals import Signal, SignalType, USER_SIGNAL_TYPES


def test_all_required_signal_types_exist():
    required = {
        "STARTED", "THINKING", "COMPLETE", "FAILED", "BLOCKED",
        "HUMAN_NEEDED", "HUMAN_RESPONDED",
        "APPROVED", "REJECTED",
        "USER_COMMENT", "USER_COMMENT_PRIORITY",
        "SCREENSHOT_READY", "USER_VISUAL_FEEDBACK",
        "GIT_COMMIT", "GIT_MERGE", "GIT_CONFLICT",
        "PACKAGE_REQUEST", "PACKAGE_APPROVED", "PACKAGE_REJECTED",
        "CREDENTIAL_REQUEST", "CREDENTIAL_GRANTED", "CREDENTIAL_DENIED",
        "FILE_CLAIM", "FILE_RELEASE", "MANIFEST_INVALIDATED",
        "PROTOCOL",
    }
    actual = {t.name for t in SignalType}
    assert required <= actual, f"missing: {required - actual}"


def test_agent_signal_basic():
    s = Signal(
        type=SignalType.STARTED,
        summary="developer began working",
        from_agent="developer",
    )
    assert s.from_agent == "developer"
    assert s.from_user is False
    assert s.priority == "normal"
    assert s.iteration == 0
    assert s.seen_by == []
    assert s.id is not None
    assert s.timestamp is not None


def test_user_signal_auto_marks_from_user():
    s = Signal(
        type=SignalType.USER_COMMENT,
        summary="please add a logo",
    )
    assert s.from_user is True
    assert s.from_agent is None


def test_user_signal_rejects_from_agent():
    with pytest.raises(ValueError, match="user-originated"):
        Signal(
            type=SignalType.USER_COMMENT,
            summary="x",
            from_agent="developer",
        )


def test_agent_signal_rejects_from_user_true():
    with pytest.raises(ValueError, match="agent-originated"):
        Signal(
            type=SignalType.STARTED,
            summary="x",
            from_agent="developer",
            from_user=True,
        )


def test_invalid_priority():
    with pytest.raises(ValueError, match="priority"):
        Signal(
            type=SignalType.STARTED,
            summary="x",
            from_agent="dev",
            priority="urgent",  # type: ignore[arg-type]
        )


def test_mark_seen_by_is_idempotent():
    s = Signal(type=SignalType.USER_COMMENT, summary="x")
    s.mark_seen_by("developer")
    s.mark_seen_by("developer")
    s.mark_seen_by("tester")
    assert s.seen_by == ["developer", "tester"]


def test_user_signal_types_set_matches_definition():
    assert SignalType.USER_COMMENT in USER_SIGNAL_TYPES
    assert SignalType.USER_COMMENT_PRIORITY in USER_SIGNAL_TYPES
    assert SignalType.USER_VISUAL_FEEDBACK in USER_SIGNAL_TYPES
    assert SignalType.HUMAN_RESPONDED in USER_SIGNAL_TYPES
    assert SignalType.STARTED not in USER_SIGNAL_TYPES


def test_high_priority_allowed():
    s = Signal(
        type=SignalType.USER_COMMENT_PRIORITY,
        summary="urgent",
        priority="high",
    )
    assert s.priority == "high"


def test_signals_have_unique_ids():
    a = Signal(type=SignalType.STARTED, summary="x", from_agent="dev")
    b = Signal(type=SignalType.STARTED, summary="x", from_agent="dev")
    assert a.id != b.id
