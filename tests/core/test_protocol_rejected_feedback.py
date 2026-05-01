"""Unit tests for M5 RejectedFeedback dataclass, render_rejected_feedback,
and VerifyResult FAIL detail validation."""

from __future__ import annotations

import pytest

from core.protocol import (
    RejectedFeedback,
    VerifyResult,
    _REJECTED_FEEDBACK_MAX_DETAIL,
    render_rejected_feedback,
)


# ── VerifyResult FAIL validation ──────────────────────────────────────────────


def test_verify_fail_empty_detail_raises():
    with pytest.raises(ValueError, match="at least 20 characters"):
        VerifyResult(checkpoint="review", status="FAIL", detail="")


def test_verify_fail_short_detail_raises():
    with pytest.raises(ValueError, match="at least 20 characters"):
        VerifyResult(checkpoint="review", status="FAIL", detail="too short")


def test_verify_fail_none_detail_raises():
    with pytest.raises(ValueError, match="at least 20 characters"):
        VerifyResult(checkpoint="review", status="FAIL", detail=None)


def test_verify_fail_exact_20_chars_passes():
    vr = VerifyResult(checkpoint="review", status="FAIL", detail="x" * 20)
    assert vr.status == "FAIL"


def test_verify_fail_long_detail_passes():
    vr = VerifyResult(
        checkpoint="review",
        status="FAIL",
        detail="The login function does not validate empty passwords.",
    )
    assert vr.status == "FAIL"


def test_verify_pass_needs_no_detail():
    vr = VerifyResult(checkpoint="review", status="PASS")
    assert vr.status == "PASS"


def test_render_includes_attempt_number():
    result = render_rejected_feedback("Alice", "reviewer", "missing tests", 1)
    assert "attempt 1" in result


def test_render_includes_reviewer_name_and_role():
    result = render_rejected_feedback("Alice", "reviewer", "detail here", 2)
    assert "Alice (reviewer)" in result


def test_render_includes_detail():
    result = render_rejected_feedback("Alice", "reviewer", "specific reason X", 1)
    assert "specific reason X" in result


def test_render_fixed_delimiters():
    result = render_rejected_feedback("Bob", "code_reviewer", "bad formatting", 3)
    assert result.startswith("=== REVIEWER REJECTED YOUR WORK (attempt 3) ===")
    assert "--- Address each point above. Re-emit COMMIT when fixed. ---" in result


def test_detail_truncated_at_limit():
    long_detail = "x" * (_REJECTED_FEEDBACK_MAX_DETAIL + 100)
    result = render_rejected_feedback("Alice", "reviewer", long_detail, 1)
    assert "…(truncated)" in result
    # After truncation the detail portion should not exceed the cap + suffix
    lines = result.split("\n")
    reason_idx = next(i for i, ln in enumerate(lines) if ln == "Reason:")
    detail_line = lines[reason_idx + 1]
    assert len(detail_line) <= _REJECTED_FEEDBACK_MAX_DETAIL + len("…(truncated)")


def test_detail_not_truncated_at_exact_limit():
    exact_detail = "y" * _REJECTED_FEEDBACK_MAX_DETAIL
    result = render_rejected_feedback("Alice", "reviewer", exact_detail, 1)
    assert "…(truncated)" not in result


def test_rejected_feedback_dataclass_slots():
    fb = RejectedFeedback(
        reviewer_role="reviewer",
        reviewer_name="Alice",
        detail="some detail",
        attempt_number=1,
    )
    assert fb.reviewer_role == "reviewer"
    assert fb.reviewer_name == "Alice"
    assert fb.detail == "some detail"
    assert fb.attempt_number == 1


def test_render_second_attempt_shows_correct_number():
    result = render_rejected_feedback("Carol", "qa", "still broken", 2)
    assert "(attempt 2)" in result
    assert "(attempt 1)" not in result
