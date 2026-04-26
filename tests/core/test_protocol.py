"""Tests for core/protocol.py — render/parse roundtrips and validation."""

from __future__ import annotations

import pytest

from core.protocol import (
    FileChange,
    ProtocolMessage,
    VerifyResult,
    parse,
    render_action,
    render_answer,
    render_ask,
    render_commit,
    render_delta,
    render_status,
    render_verify,
)


# ── VerifyResult validation ─────────────────────────────────────────────────


def test_verify_fail_requires_detail():
    with pytest.raises(ValueError, match="requires a specific detail"):
        VerifyResult(checkpoint="token_expiry", status="FAIL")


def test_verify_fail_rejects_blank_detail():
    with pytest.raises(ValueError, match="requires a specific detail"):
        VerifyResult(checkpoint="x", status="FAIL", detail="   ")


def test_verify_pass_no_detail_ok():
    r = VerifyResult(checkpoint="x", status="PASS")
    assert r.detail is None


def test_filechange_mod_requires_lines_changed():
    with pytest.raises(ValueError, match="lines_changed"):
        FileChange(path="x.py", status="mod", description="x")


# ── COMMIT ──────────────────────────────────────────────────────────────────


def test_commit_render_and_parse_roundtrip():
    files = [
        FileChange("src/auth.py", "new", "OAuth2 flow, JWT signing"),
        FileChange("src/auth_utils.py", "new", "token validation helpers"),
        FileChange("src/api.py", "mod", "added /auth endpoints", lines_changed=14),
        FileChange("src/old_login.py", "del"),
    ]
    raw = render_commit(
        agent="developer",
        commit_hash="a3f9c2",
        files=files,
        checkpoints=["auth_flow", "token_expiry", "endpoint_401", "no_plaintext_secrets"],
    )
    msg = parse(raw)
    assert msg.type == "COMMIT"
    assert msg.from_agent == "developer"
    assert msg.commit_hash == "a3f9c2"

    parsed_files = msg.parsed["files"]
    assert len(parsed_files) == 4
    assert parsed_files[0].path == "src/auth.py"
    assert parsed_files[0].status == "new"
    assert parsed_files[0].description == "OAuth2 flow, JWT signing"
    assert parsed_files[2].status == "mod"
    assert parsed_files[2].lines_changed == 14
    assert parsed_files[3].status == "del"

    assert msg.parsed["checkpoints"] == [
        "auth_flow", "token_expiry", "endpoint_401", "no_plaintext_secrets"
    ]


def test_commit_missing_verify_raises():
    raw = "COMMIT developer abc123\n  + foo.py  [new]  desc"
    with pytest.raises(ValueError, match="VERIFY"):
        parse(raw)


# ── VERIFY_RESULT ───────────────────────────────────────────────────────────


def test_verify_result_render_and_parse_roundtrip():
    results = [
        VerifyResult("auth_flow", "PASS"),
        VerifyResult("token_expiry", "FAIL", "exp claim missing from JWT payload"),
        VerifyResult("endpoint_401", "PASS"),
    ]
    raw = render_verify(
        commit_hash="a3f9c2",
        results=results,
        action=("developer", "FIX", "token_expiry"),
    )
    msg = parse(raw)
    assert msg.type == "VERIFY_RESULT"
    assert msg.commit_hash == "a3f9c2"
    parsed_results = msg.parsed["results"]
    assert len(parsed_results) == 3
    assert parsed_results[1].status == "FAIL"
    assert parsed_results[1].detail == "exp claim missing from JWT payload"
    assert msg.parsed["action"] == {
        "target_role": "developer", "verb": "FIX", "subject": "token_expiry"
    }


def test_verify_result_without_action():
    results = [VerifyResult("x", "PASS")]
    raw = render_verify(commit_hash="abc", results=results)
    msg = parse(raw)
    assert "action" not in msg.parsed


# ── DELTA ───────────────────────────────────────────────────────────────────


def test_delta_render_and_parse_roundtrip():
    raw = render_delta(
        agent="developer",
        iteration=2,
        from_hash="a3f9c2",
        to_hash="b7d441",
        changes=[
            "src/auth.py  added exp claim to JWT payload — addresses token_expiry FAIL",
            "src/auth.py  added refresh token endpoint",
        ],
        addressed=["token_expiry addressed"],
    )
    msg = parse(raw)
    assert msg.type == "DELTA"
    assert msg.from_agent == "developer"
    assert msg.commit_hash == "b7d441"
    assert msg.parsed["iteration"] == 2
    assert msg.parsed["from_hash"] == "a3f9c2"
    assert msg.parsed["to_hash"] == "b7d441"
    assert len(msg.parsed["changes"]) == 2
    assert msg.parsed["addressed"] == ["token_expiry addressed"]


# ── ACTION ──────────────────────────────────────────────────────────────────


def test_action_render_and_parse_roundtrip():
    raw = render_action("developer", "FIX", "token_expiry")
    msg = parse(raw)
    assert msg.type == "ACTION"
    assert msg.parsed == {
        "target_role": "developer",
        "verb": "FIX",
        "subject": "token_expiry",
    }


def test_action_invalid_verb():
    with pytest.raises(ValueError, match="invalid action verb"):
        render_action("developer", "DELETE", "x")  # type: ignore[arg-type]


# ── ASK / ANSWER ────────────────────────────────────────────────────────────


def test_ask_render_and_parse_with_context():
    raw = render_ask(
        question_id="q1",
        from_role="developer",
        to_role="architect",
        question="should we use Redis or in-memory cache?",
        context="cache layer for session tokens",
    )
    msg = parse(raw)
    assert msg.type == "ASK"
    assert msg.parsed["question_id"] == "q1"
    assert msg.parsed["from_role"] == "developer"
    assert msg.parsed["to_role"] == "architect"
    assert msg.parsed["question"] == "should we use Redis or in-memory cache?"
    assert msg.parsed["context"] == "cache layer for session tokens"


def test_ask_render_and_parse_without_context():
    raw = render_ask("q2", "dev", "arch", "is this fine?")
    msg = parse(raw)
    assert msg.parsed["context"] is None


def test_ask_accepts_ascii_arrow():
    msg = parse("ASK q1 developer -> architect\n  question?")
    assert msg.type == "ASK"
    assert msg.parsed["from_role"] == "developer"
    assert msg.parsed["to_role"] == "architect"


def test_answer_render_and_parse_roundtrip():
    raw = render_answer("q1", to_role="architect", from_role="developer", answer="use Redis")
    msg = parse(raw)
    assert msg.type == "ANSWER"
    assert msg.parsed["question_id"] == "q1"
    assert msg.parsed["answer"] == "use Redis"


# ── STATUS ──────────────────────────────────────────────────────────────────


def test_status_render_and_parse_roundtrip():
    raw = render_status("developer", "working", 3, "writing auth module")
    msg = parse(raw)
    assert msg.type == "STATUS"
    assert msg.from_agent == "developer"
    assert msg.parsed == {
        "state": "working",
        "iteration": 3,
        "description": "writing auth module",
    }


# ── parse() error handling ──────────────────────────────────────────────────


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        parse("")


def test_parse_unknown_type_raises():
    with pytest.raises(ValueError, match="unknown protocol message type"):
        parse("WIBBLE foo bar")


def test_parse_invalid_protocol_message_type_raises():
    with pytest.raises(ValueError):
        ProtocolMessage(type="INVALID", from_agent=None, commit_hash=None, raw="x")
