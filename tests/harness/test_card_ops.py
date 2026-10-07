"""Tests for core.harness.card_ops (humans work the task board, S34)."""

from __future__ import annotations

import pytest

from core.harness.card_ops import (
    CardOpError,
    card_view,
    human_add_card,
    human_comment,
    human_set_priority,
)
from core.harness.task_board import TaskBoard
from core.harness.tasks import PlanResult, PlannedTask
from core.signals import SignalType
from core.workspace import Workspace


def _ws() -> Workspace:
    ws = Workspace(run_id="r", goal="g")
    TaskBoard(ws).create_from_plan(PlanResult(tasks=[
        PlannedTask(id="T1", title="First card title", owner_role="backend",
                    acceptance=["it works"], size="S", kind="code"),
        PlannedTask(id="T2", title="Second card title", owner_role="frontend",
                    acceptance=["it renders"], size="M", kind="code", depends_on=["T1"]),
    ]), "planner")
    return ws


def _add(ws: Workspace, **kw):
    args = dict(owner="backend", title="Add a health route", acceptance=["GET /health is 200"])
    args.update(kw)
    return human_add_card(ws, **args)


def test_add_card_ok() -> None:
    ws = _ws()
    card = _add(ws, priority=3, depends_on=["T1"], files_hint=["api/health.py"])
    assert card.story_id == "board" and card.column == "todo"
    assert card.assigned_to == "backend" and card.priority == 3
    assert card.depends_on == ["T1"] and card.created_by == "human"
    assert card.thread[0]["kind"] == "human_comment"
    assert card.thread[0]["text"] == "added by a human"
    assert card in TaskBoard(ws).cards("backend")


def test_add_card_owner_from_agent_states() -> None:
    from core.workspace import AgentState
    ws = _ws()
    ws.agent_states["qa"] = AgentState.RUNNING
    assert _add(ws, owner="qa").assigned_to == "qa"


@pytest.mark.parametrize("kwargs, fragment", [
    (dict(owner="ghost"), "unknown owner"),
    (dict(owner=""), "unknown owner"),
    (dict(title="abc"), "title"),
    (dict(title="     "), "title"),
    (dict(acceptance=[]), "acceptance"),
    (dict(acceptance=["  ", ""]), "acceptance"),
    (dict(kind="magic"), "kind"),
    (dict(size="L"), "split it"),
    (dict(size="XL"), "size"),
    (dict(depends_on=["T99"]), "T99"),
    (dict(priority=True), "priority"),
])
def test_add_card_rejects_bad_input(kwargs, fragment) -> None:
    ws = _ws()
    before = len(ws.tasks)
    with pytest.raises(CardOpError, match=fragment):
        _add(ws, **kwargs)
    assert len(ws.tasks) == before


def test_card_op_error_is_value_error() -> None:
    assert issubclass(CardOpError, ValueError)


def test_new_card_ids_do_not_collide() -> None:
    ws = _ws()
    assert _add(ws).task_id == "T3"
    assert _add(ws).task_id == "T4"


def test_set_priority_persists_and_notes() -> None:
    ws = _ws()
    persisted = []
    ws.set_task_persister(lambda t: persisted.append((t.task_id, t.priority)))
    card = human_set_priority(ws, "T1", 7)
    assert card.priority == 7 and ws.tasks["T1"].priority == 7
    assert ("T1", 7) in persisted
    assert card.thread[-1]["kind"] == "human_comment"
    assert card.thread[-1]["text"] == "priority set to 7"
    threads = [s for s in ws.board if s.type is SignalType.TASK_THREAD]
    assert threads and "priority set to 7" in threads[-1].summary


def test_set_priority_changes_next_for() -> None:
    ws = _ws()
    a = _add(ws)
    board = TaskBoard(ws)
    assert board.next_for("backend").task_id == "T1"
    human_set_priority(ws, a.task_id, 10)
    assert board.next_for("backend").task_id == a.task_id


def test_set_priority_refuses_done_and_unknown() -> None:
    ws = _ws()
    ws.tasks["T1"].column = "done"
    with pytest.raises(CardOpError, match="done"):
        human_set_priority(ws, "T1", 5)
    with pytest.raises(KeyError):
        human_set_priority(ws, "nope", 5)
    with pytest.raises(CardOpError):
        human_set_priority(ws, "T2", "high")  # type: ignore[arg-type]


def test_comment_appears_in_next_brief() -> None:
    ws = _ws()
    card = human_comment(ws, "T1", "  please use the v2 endpoint  ", author="erik")
    entry = card.thread[-1]
    assert entry["kind"] == "human_comment" and entry["author"] == "erik"
    assert entry["text"] == "please use the v2 endpoint"
    brief = TaskBoard(ws).brief_for("T1")
    assert "please use the v2 endpoint" in brief
    assert "please use the v2 endpoint" not in TaskBoard(ws).brief_for("T2")


def test_added_card_brief_mentions_human() -> None:
    ws = _ws()
    card = _add(ws)
    assert "added by a human" in TaskBoard(ws).brief_for(card.task_id)


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001])
def test_comment_rejects_bad_text(text) -> None:
    ws = _ws()
    with pytest.raises(CardOpError):
        human_comment(ws, "T1", text)
    assert ws.tasks["T1"].thread == []


def test_comment_allows_max_length_and_unknown_card() -> None:
    ws = _ws()
    human_comment(ws, "T1", "x" * 4000)
    with pytest.raises(KeyError):
        human_comment(ws, "T404", "hello")


def test_listeners_called_on_each_mutation() -> None:
    ws = _ws()
    calls: list[int] = []
    ws.board_listeners.append(lambda: calls.append(1))
    _add(ws)
    human_set_priority(ws, "T1", 2)
    human_comment(ws, "T1", "hello")
    assert len(calls) == 3


def test_listeners_not_called_on_failure() -> None:
    ws = _ws()
    calls: list[int] = []
    ws.board_listeners.append(lambda: calls.append(1))
    with pytest.raises(CardOpError):
        _add(ws, size="L")
    assert calls == []


def test_failing_listener_does_not_break_op_or_others() -> None:
    ws = _ws()
    calls: list[int] = []

    def boom() -> None:
        raise RuntimeError("listener bug")

    ws.board_listeners.extend([boom, lambda: calls.append(1)])
    human_comment(ws, "T1", "hello")
    assert calls == [1] and ws.tasks["T1"].thread


def test_board_listeners_not_in_repr() -> None:
    ws = _ws()
    ws.board_listeners.append(lambda: None)
    assert "board_listeners" not in repr(ws)


def test_card_view_shape() -> None:
    ws = _ws()
    human_comment(ws, "T2", "note")
    view = card_view(ws.tasks["T2"])
    assert set(view) == {"id", "title", "owner", "column", "kind", "size", "priority",
                         "review_round", "acceptance", "depends_on", "files_hint",
                         "visual_refs", "column_since", "thread", "backlog_id", "serves",
                         "supports"}
    assert view["id"] == "T2" and view["owner"] == "frontend" and view["depends_on"] == ["T1"]
    assert view["thread"][0]["text"] == "note"
