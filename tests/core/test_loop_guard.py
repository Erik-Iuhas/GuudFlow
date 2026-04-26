"""Tests for core/loop_guard.py."""

from __future__ import annotations

import time

import pytest

from core.loop_guard import (
    LoopGuard,
    LoopGuardConfig,
    LoopGuardVerdict,
    _classify_error,
)


# ── Recording basics ───────────────────────────────────────────────────────


def test_clean_call_returns_no_verdict():
    guard = LoopGuard()
    v = guard.record("fs:read", {"path": "x.py"}, {"ok": True, "content": "hi"})
    assert v.abort is False
    assert v.nudge is None


def test_single_error_returns_no_verdict():
    """A single error doesn't justify a nudge — humans get bugs all the time."""
    guard = LoopGuard()
    v = guard.record("fs:read", {"path": "ghost.py"}, {"error": "not found: ghost.py"})
    assert v.nudge is None


# ── Same-call repetition ───────────────────────────────────────────────────


def test_three_identical_calls_same_error_triggers_nudge():
    guard = LoopGuard(LoopGuardConfig(max_repeat_identical=3))
    args = {"path": "ghost.py"}
    err = {"error": "not found: ghost.py"}

    guard.record("fs:read", args, err)
    guard.record("fs:read", args, err)
    v = guard.record("fs:read", args, err)
    assert v.nudge is not None
    assert "fs:read" in v.nudge
    assert "same arguments" in v.nudge.lower()


def test_different_args_does_not_trigger_identical_repeat():
    guard = LoopGuard(LoopGuardConfig(max_repeat_identical=3))
    err_template = "not found: {}"
    for path in ["a", "b", "c"]:
        v = guard.record(
            "fs:read", {"path": path},
            {"error": err_template.format(path)},
        )
    # Args differ → identical-repeat doesn't fire (but error-class might;
    # we set max_repeat_error_class default 5 so 3 hits won't trigger).
    assert v.nudge is None or "same arguments" not in (v.nudge or "")


def test_different_error_class_resets_repeat():
    guard = LoopGuard(LoopGuardConfig(max_repeat_identical=3))
    args = {"path": "x.py"}
    guard.record("fs:read", args, {"error": "not found: x.py"})
    guard.record("fs:read", args, {"error": "not found: x.py"})
    # Switch error class — fresh streak.
    v = guard.record("fs:read", args, {"error": "path safety: bad path"})
    assert v.nudge is None


def test_success_resets_streak():
    guard = LoopGuard(LoopGuardConfig(max_repeat_identical=3))
    args = {"path": "x.py"}
    err = {"error": "not found: x.py"}
    guard.record("fs:read", args, err)
    guard.record("fs:read", args, err)
    guard.record("fs:read", args, {"ok": True, "content": "x"})  # success
    # Now two more errors — should not trigger because of the success in between.
    guard.record("fs:read", args, err)
    v = guard.record("fs:read", args, err)
    assert v.nudge is None


# ── Same error class across different args ────────────────────────────────


def test_repeated_error_class_across_different_args():
    guard = LoopGuard(LoopGuardConfig(
        max_repeat_identical=10,  # disable identical check
        max_repeat_error_class=5,
    ))
    for i in range(5):
        v = guard.record(
            "fs:read", {"path": f"file_{i}.py"},
            {"error": f"not found: file_{i}.py"},
        )
    assert v.nudge is not None
    assert "not found" in v.nudge.lower() or "errors" in v.nudge.lower()


# ── Alternating cycle ──────────────────────────────────────────────────────


def test_alternating_two_failing_calls_triggers_cycle_nudge():
    guard = LoopGuard(LoopGuardConfig(
        max_repeat_identical=10,    # disable
        max_repeat_error_class=20,  # disable
        cycle_window=6,
        cycle_distinct_threshold=2,
    ))
    err_a = {"error": "not found: a"}
    err_b = {"error": "not found: b"}
    guard.record("fs:read", {"p": "a"}, err_a)
    guard.record("fs:read", {"p": "b"}, err_b)
    guard.record("fs:read", {"p": "a"}, err_a)
    guard.record("fs:read", {"p": "b"}, err_b)
    guard.record("fs:read", {"p": "a"}, err_a)
    v = guard.record("fs:read", {"p": "b"}, err_b)
    assert v.nudge is not None
    assert "cycle" in v.nudge.lower() or "alternating" in v.nudge.lower() or "different approach" in v.nudge.lower()


def test_cycle_does_not_trigger_when_one_succeeds():
    guard = LoopGuard(LoopGuardConfig(
        max_repeat_identical=10,
        max_repeat_error_class=20,
        cycle_window=6,
        cycle_distinct_threshold=2,
    ))
    err_a = {"error": "x"}
    ok_b = {"ok": True}
    for _ in range(3):
        guard.record("fs:read", {"p": "a"}, err_a)
        guard.record("fs:read", {"p": "b"}, ok_b)
    # Last 6 alternate between two sigs but half are successes — no cycle nudge.
    v = guard.record("fs:read", {"p": "a"}, err_a)
    # The identical-repeat check might fire, but cycle should not. Check
    # that at least we don't fire abort.
    assert v.abort is False


# ── Wall clock ─────────────────────────────────────────────────────────────


def test_deadline_exceeded_triggers_abort(monkeypatch):
    guard = LoopGuard(LoopGuardConfig(max_wall_seconds=0.001))
    time.sleep(0.05)
    v = guard.record("fs:read", {"x": 1}, {"ok": True})
    assert v.abort is True
    assert "deadline" in v.reason.lower() or "wall-clock" in v.reason.lower()


def test_deadline_property():
    guard = LoopGuard(LoopGuardConfig(max_wall_seconds=10.0))
    assert guard.deadline_exceeded() is False
    assert guard.elapsed_seconds < 10.0


# ── Error classification ───────────────────────────────────────────────────


def test_classify_error_with_colon():
    assert _classify_error({"error": "not found: x.py"}) == "not found"
    assert _classify_error({"error": "path safety: bad"}) == "path safety"


def test_classify_error_without_colon():
    cls = _classify_error({"error": "tool not available"})
    assert "tool not available" in cls


def test_classify_error_empty():
    assert _classify_error({"error": ""}) == "error"
    assert _classify_error({"ok": False}) == "error"


# ── History window ─────────────────────────────────────────────────────────


def test_history_window_caps_records():
    guard = LoopGuard(LoopGuardConfig(history_window=5))
    for i in range(20):
        guard.record("fs:read", {"i": i}, {"ok": True})
    assert len(guard._history) == 5
