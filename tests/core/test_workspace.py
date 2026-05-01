"""Tests for core/workspace.py."""

from __future__ import annotations

import uuid

import pytest

from core.signals import Signal, SignalType
from core.workspace import (
    AgentState,
    Artifact,
    ContextSnapshot,
    GitState,
    HumanRequest,
    IterationMetrics,
    RepoMap,
    USER_BOARD_TYPES,
    Workspace,
)


def test_workspace_initialises_with_defaults():
    ws = Workspace(run_id="r1", goal="build a thing")
    assert ws.run_id == "r1"
    assert ws.iteration == 0
    assert ws.phase == "init"
    assert ws.board == []
    assert ws.user_board == []
    assert ws.artifacts == {}
    assert ws.files is None
    assert ws.embeddings_enabled is False
    assert isinstance(ws.git, GitState)
    assert isinstance(ws.repo_map, RepoMap)
    assert ws.pending_human is None


def test_post_agent_signal_only_on_main_board():
    ws = Workspace(run_id="r1", goal="x")
    s = Signal(type=SignalType.STARTED, summary="dev started", from_agent="developer")
    ws.post_signal(s)
    assert ws.board == [s]
    assert ws.user_board == []


def test_post_user_comment_mirrored_to_user_board():
    ws = Workspace(run_id="r1", goal="x")
    s = Signal(type=SignalType.USER_COMMENT, summary="please add a logo")
    ws.post_signal(s)
    assert s in ws.board
    assert s in ws.user_board


def test_post_user_priority_comment_mirrored():
    ws = Workspace(run_id="r1", goal="x")
    s = Signal(type=SignalType.USER_COMMENT_PRIORITY, summary="urgent", priority="high")
    ws.post_signal(s)
    assert s in ws.user_board


def test_post_user_visual_feedback_mirrored():
    ws = Workspace(run_id="r1", goal="x")
    s = Signal(type=SignalType.USER_VISUAL_FEEDBACK, summary="looks great")
    ws.post_signal(s)
    assert s in ws.user_board


def test_human_responded_not_on_user_board():
    """HUMAN_RESPONDED is user-originated but is a control signal, not ambient."""
    ws = Workspace(run_id="r1", goal="x")
    s = Signal(type=SignalType.HUMAN_RESPONDED, summary="answer")
    ws.post_signal(s)
    assert s in ws.board
    assert s not in ws.user_board


def test_mark_seen_records_role():
    ws = Workspace(run_id="r1", goal="x")
    s = Signal(type=SignalType.USER_COMMENT, summary="x")
    ws.post_signal(s)
    ws.mark_seen(s.id, "developer")
    assert "developer" in s.seen_by


def test_mark_seen_idempotent():
    ws = Workspace(run_id="r1", goal="x")
    s = Signal(type=SignalType.USER_COMMENT, summary="x")
    ws.post_signal(s)
    ws.mark_seen(s.id, "developer")
    ws.mark_seen(s.id, "developer")
    assert s.seen_by == ["developer"]


def test_mark_seen_unknown_signal_raises():
    ws = Workspace(run_id="r1", goal="x")
    with pytest.raises(KeyError):
        ws.mark_seen(uuid.uuid4(), "developer")


def test_unseen_user_comments_filters_correctly():
    ws = Workspace(run_id="r1", goal="x")
    s1 = Signal(type=SignalType.USER_COMMENT, summary="a")
    s2 = Signal(type=SignalType.USER_COMMENT, summary="b")
    ws.post_signal(s1)
    ws.post_signal(s2)
    ws.mark_seen(s1.id, "developer")

    assert ws.unseen_user_comments("developer") == [s2]
    assert ws.unseen_user_comments("tester") == [s1, s2]


def test_unseen_user_comments_ignores_main_board_only_signals():
    ws = Workspace(run_id="r1", goal="x")
    started = Signal(type=SignalType.STARTED, summary="x", from_agent="dev")
    comment = Signal(type=SignalType.USER_COMMENT, summary="x")
    ws.post_signal(started)
    ws.post_signal(comment)
    assert ws.unseen_user_comments("developer") == [comment]


def test_signals_of_type_filters():
    ws = Workspace(run_id="r1", goal="x")
    a = Signal(type=SignalType.STARTED, summary="x", from_agent="dev")
    b = Signal(type=SignalType.COMPLETE, summary="x", from_agent="dev")
    c = Signal(type=SignalType.STARTED, summary="x", from_agent="tester")
    ws.post_signal(a)
    ws.post_signal(b)
    ws.post_signal(c)
    assert ws.signals_of_type(SignalType.STARTED) == [a, c]
    assert ws.signals_of_type(SignalType.STARTED, SignalType.COMPLETE) == [a, b, c]
    assert ws.signals_of_type(SignalType.FAILED) == []


def test_artifact_typed_text():
    a = Artifact(agent_role="developer", type="code", content="print('x')", iteration=1)
    assert a.agent_role == "developer"
    assert a.type == "code"
    assert isinstance(a.content, str)


def test_artifact_typed_structured():
    spec = {"endpoints": [{"path": "/x", "method": "GET"}]}
    a = Artifact(agent_role="architect", type="api_spec", content=spec, iteration=2)
    assert isinstance(a.content, dict)
    assert a.content["endpoints"][0]["path"] == "/x"


def test_pending_human_request_constructs():
    ws = Workspace(run_id="r1", goal="x")
    ws.pending_human = HumanRequest(
        from_agent="requirements",
        question="Should this support mobile?",
        context="Discovered ambiguity in goal",
        iteration=1,
    )
    assert ws.pending_human.from_agent == "requirements"


def test_agent_state_enum_values():
    assert AgentState.PENDING.value == "pending"
    assert AgentState.RUNNING.value == "running"
    assert AgentState.COMPLETE.value == "complete"
    assert AgentState.WAITING_FOR_HUMAN.value == "waiting_for_human"


def test_user_board_types_set_is_correct():
    assert SignalType.USER_COMMENT in USER_BOARD_TYPES
    assert SignalType.USER_COMMENT_PRIORITY in USER_BOARD_TYPES
    assert SignalType.USER_VISUAL_FEEDBACK in USER_BOARD_TYPES
    assert SignalType.HUMAN_RESPONDED not in USER_BOARD_TYPES
    assert SignalType.STARTED not in USER_BOARD_TYPES


def test_board_append_order_preserved():
    ws = Workspace(run_id="r1", goal="x")
    sigs = [
        Signal(type=SignalType.STARTED, summary=f"s{i}", from_agent="dev")
        for i in range(5)
    ]
    for s in sigs:
        ws.post_signal(s)
    assert ws.board == sigs


# ── Track 1: IterationMetrics + ContextSnapshot ──────────────────────────────


def test_add_iteration_metrics_appends_to_list():
    ws = Workspace(run_id="r1", goal="x")
    m = IterationMetrics(run_id="r1", role="developer", iteration=1, model="claude-sonnet-4-6")
    ws.add_iteration_metrics(m)
    assert len(ws.iteration_metrics) == 1
    assert ws.iteration_metrics[0] is m


def test_add_iteration_metrics_fires_persister():
    ws = Workspace(run_id="r1", goal="x")
    fired: list[IterationMetrics] = []
    ws._iteration_metrics_persister = fired.append

    m = IterationMetrics(run_id="r1", role="reviewer", iteration=2, input_tokens=100)
    ws.add_iteration_metrics(m)

    assert len(fired) == 1
    assert fired[0] is m


def test_add_iteration_metrics_swallows_persister_exception():
    ws = Workspace(run_id="r1", goal="x")

    def _bad(m: IterationMetrics) -> None:
        raise RuntimeError("disk full")

    ws._iteration_metrics_persister = _bad
    m = IterationMetrics(run_id="r1", role="dev", iteration=1)
    ws.add_iteration_metrics(m)  # must not raise
    assert ws.iteration_metrics == [m]


def test_record_context_snapshot_appends_and_fires_persister():
    ws = Workspace(run_id="r1", goal="x")
    fired: list[ContextSnapshot] = []
    ws._context_log_persister = fired.append

    snap = ContextSnapshot(
        run_id="r1",
        role="developer",
        iteration=1,
        task="implement auth",
        used_tokens=3000,
    )
    ws.record_context_snapshot(snap)

    assert ws.context_log == [snap]
    assert fired == [snap]


def test_record_context_snapshot_swallows_persister_exception():
    ws = Workspace(run_id="r1", goal="x")

    def _bad(s: ContextSnapshot) -> None:
        raise OSError("permission denied")

    ws._context_log_persister = _bad
    snap = ContextSnapshot(run_id="r1", role="dev", iteration=1, task="x")
    ws.record_context_snapshot(snap)  # must not raise
    assert ws.context_log == [snap]
