"""Protocol wire format for task-lifecycle messages — M3.5 Chunk 2.

Covers ASSIGN_TASK, TASK_STARTED, TASK_COMPLETED, TASK_BLOCKED,
TASK_CANCELLED, and the COMMIT `closes:` trailer. Existing COMMIT
parsing must remain byte-identical for callers that don't pass
`closes` — that's the M1-back-compat regression guard.
"""

from __future__ import annotations

import pytest

from core.protocol import (
    FileChange,
    TaskRefField,
    parse,
    render_assign_task,
    render_commit,
    render_task_blocked,
    render_task_cancelled,
    render_task_completed,
    render_task_started,
)


# ── ASSIGN_TASK roundtrip ───────────────────────────────────────────────────


def test_assign_task_roundtrip_minimal() -> None:
    raw = render_assign_task(
        "developer",
        "abc123",
        goal="implement the JWT issuer",
    )
    msg = parse(raw)
    assert msg.type == "ASSIGN_TASK"
    assert msg.parsed["assignee"] == "developer"
    assert msg.parsed["task_id"] == "abc123"
    assert msg.parsed["goal"] == "implement the JWT issuer"
    assert msg.parsed["acceptance"] == []


def test_assign_task_roundtrip_full() -> None:
    raw = render_assign_task(
        "developer",
        "abc123",
        goal="build issuer",
        acceptance=["rs256 signing", "key rotation", "1000 rpm"],
        out_of_scope=["oauth2 social"],
        parent_task="parent42",
        story="story_jwt",
        references=[
            TaskRefField("file", "docs/auth.md", "design source"),
            TaskRefField("commit", "def456", "architect commit"),
        ],
    )
    msg = parse(raw)
    assert msg.parsed["acceptance"] == ["rs256 signing", "key rotation", "1000 rpm"]
    assert msg.parsed["out_of_scope"] == ["oauth2 social"]
    assert msg.parsed["parent_task"] == "parent42"
    assert msg.parsed["story_id"] == "story_jwt"
    refs = msg.parsed["references"]
    assert len(refs) == 2
    assert refs[0].kind == "file"
    assert refs[0].value == "docs/auth.md"
    assert refs[0].note == "design source"
    assert refs[1].kind == "commit"


def test_assign_task_renders_pipe_separator() -> None:
    raw = render_assign_task(
        "dev", "t1", goal="g", acceptance=["a", "b"], out_of_scope=["x"],
    )
    assert "acceptance: a | b" in raw
    assert "out_of_scope: x" in raw


# ── TASK_* lifecycle messages ───────────────────────────────────────────────


def test_task_started_roundtrip() -> None:
    raw = render_task_started("t1", "developer")
    msg = parse(raw)
    assert msg.type == "TASK_STARTED"
    assert msg.parsed == {"task_id": "t1", "by_role": "developer"}
    assert msg.from_agent == "developer"


def test_task_completed_roundtrip() -> None:
    raw = render_task_completed("t1", "abc1234")
    msg = parse(raw)
    assert msg.type == "TASK_COMPLETED"
    assert msg.parsed["task_id"] == "t1"
    assert msg.parsed["commit_hash"] == "abc1234"
    assert msg.commit_hash == "abc1234"


def test_task_blocked_roundtrip() -> None:
    raw = render_task_blocked(
        "t1", "developer",
        reason="needs issuer",
        blocked_on=["t2", "t3"],
    )
    msg = parse(raw)
    assert msg.type == "TASK_BLOCKED"
    assert msg.parsed["reason"] == "needs issuer"
    assert msg.parsed["blocked_on"] == ["t2", "t3"]


def test_task_cancelled_roundtrip() -> None:
    raw = render_task_cancelled("t1", "tech_lead", reason="superseded")
    msg = parse(raw)
    assert msg.type == "TASK_CANCELLED"
    assert msg.parsed == {
        "task_id": "t1", "by_role": "tech_lead", "reason": "superseded",
    }


# ── COMMIT closes: extension ────────────────────────────────────────────────


def test_commit_without_closes_back_compat() -> None:
    """M1 regression guard. render_commit() without the new `closes`
    kwarg must produce byte-identical output."""
    fc = FileChange(path="src/x.py", status="new")
    raw = render_commit("dev", "abc1234", [fc], ["builds"])
    expected = (
        "COMMIT dev abc1234\n"
        "  + src/x.py  [new]\n"
        "VERIFY: builds"
    )
    assert raw == expected
    msg = parse(raw)
    assert msg.type == "COMMIT"
    assert "closes" not in msg.parsed  # absent, not None


def test_commit_with_closes_renders_and_parses() -> None:
    fc = FileChange(path="src/x.py", status="new")
    raw = render_commit(
        "dev", "abc1234", [fc], ["builds"],
        closes=["task:t1", "task:t2"],
    )
    assert "closes: task:t1 | task:t2" in raw
    msg = parse(raw)
    assert msg.parsed["closes"] == ["task:t1", "task:t2"]


def test_commit_closes_filter_blanks() -> None:
    fc = FileChange(path="x", status="new")
    raw = render_commit("dev", "h", [fc], ["ok"], closes=["", "task:t1", "  "])
    msg = parse(raw)
    assert msg.parsed["closes"] == ["task:t1"]


def test_commit_with_empty_closes_list_omits_trailer() -> None:
    fc = FileChange(path="x", status="new")
    raw = render_commit("dev", "h", [fc], ["ok"], closes=[])
    assert "closes:" not in raw
    msg = parse(raw)
    assert "closes" not in msg.parsed


def test_commit_parses_existing_messages_with_no_closes() -> None:
    raw = (
        "COMMIT dev hashabc\n"
        "  + foo.py  [new]\n"
        "  ~ bar.py  [mod:5]\n"
        "VERIFY: builds | typechecks"
    )
    msg = parse(raw)
    assert "closes" not in msg.parsed
    assert msg.parsed["checkpoints"] == ["builds", "typechecks"]


# ── Malformed input ─────────────────────────────────────────────────────────


def test_unknown_assign_task_line_raises() -> None:
    raw = "ASSIGN_TASK dev task:t1\n  bogus_field: value"
    with pytest.raises(ValueError):
        parse(raw)


def test_malformed_task_ref_raises() -> None:
    raw = "ASSIGN_TASK dev task:t1\n  goal: x\n  refs:\n    not_a_ref_format"
    with pytest.raises(ValueError):
        parse(raw)


def test_assign_task_inline_ref_after_refs_label() -> None:
    raw = (
        "ASSIGN_TASK dev task:t1\n"
        "  goal: g\n"
        '  refs: file:foo.md "note"'
    )
    msg = parse(raw)
    refs = msg.parsed["references"]
    assert len(refs) == 1
    assert refs[0].value == "foo.md"
    assert refs[0].note == "note"
