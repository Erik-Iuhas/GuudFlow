"""Tests for core.harness.task_board (plan §4.6, O23)."""

from __future__ import annotations

import json

import pytest

from core.harness.results import AcceptanceCheck, AgentResult, Issue, ItemVerdict, ReviewResult
from core.harness.task_board import BoardLimits, TaskBoard, _overlaps
from core.harness.tasks import PlanResult, PlannedTask
from core.signals import SignalType
from core.workspace import Workspace


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _task(tid: str, owner: str, *, deps: list[str] | None = None, files: list[str] | None = None,
          size: str = "S", kind: str = "code") -> PlannedTask:
    return PlannedTask(id=tid, title=f"Card {tid} title", owner_role=owner,
                       acceptance=[f"{tid} item one works", f"{tid} item two works"],
                       depends_on=deps or [], files_hint=files or [], size=size, kind=kind)


def _board(*tasks: PlannedTask, reviewers: dict[str, str] | None = None,
           limits: BoardLimits | None = None) -> tuple[TaskBoard, Workspace, Clock]:
    ws = Workspace(run_id="r", goal="g")
    clock = Clock()
    board = TaskBoard(ws, reviewers_of=reviewers or {}, review_rounds_for={"rev": 2},
                      limits=limits or BoardLimits(), clock=clock)
    board.create_from_plan(PlanResult(tasks=list(tasks)), "planner")
    return board, ws, clock


def _done(tid: str) -> AgentResult:
    return AgentResult(status="done", task_id=tid, summary="Implemented the card fully.",
                       acceptance=[AcceptanceCheck(item=f"{tid} item one works", met=True,
                                                   evidence="pytest test_one passes"),
                                   AcceptanceCheck(item=f"{tid} item two works", met=True,
                                                   evidence="pytest test_two passes")])


def _review(tid: str, passed: bool) -> ReviewResult:
    items = [ItemVerdict(item=f"{tid} item one works", met=True, detail=""),
             ItemVerdict(item=f"{tid} item two works", met=passed,
                         detail="" if passed else "returns None instead of the status dict")]
    return ReviewResult(verdict="pass" if passed else "fail", task_id=tid, items=items,
                        summary="looks good" if passed else "")


def test_plan_creates_todo_cards_in_priority_order() -> None:
    board, ws, _ = _board(_task("T1", "a"), _task("T2", "a"))
    assert [c.column for c in board.cards()] == ["todo", "todo"]
    assert board.next_for("a").task_id == "T1"
    assert sum(1 for s in ws.board if s.type is SignalType.TASK_ASSIGNED) == 2


def test_dependencies_gate_cards() -> None:
    board, _, _ = _board(_task("T1", "a"), _task("T2", "b", deps=["T1"]))
    assert board.next_for("b") is None
    board.start("a", "T1")
    board.submitted("T1", _done("T1"))
    assert board.card("T1").column == "done"           # nobody reviews a
    assert board.next_for("b").task_id == "T2"


def test_file_overlap_serialises_cards_across_roles() -> None:
    board, _, _ = _board(_task("T1", "a", files=["src/api/"]),
                         _task("T2", "b", files=["src/api/client.py"]),
                         _task("T3", "b", files=["docs/readme.md"]))
    board.start("a", "T1")
    assert board.next_for("b").task_id == "T3"          # T2 overlaps a's active card
    board.finish_turn("a")
    assert board.next_for("b").task_id == "T2"


def test_overlap_helper() -> None:
    assert _overlaps(["src/a.py"], ["src/a.py"])
    assert _overlaps(["src/**"], ["src/x/y.py"])
    assert not _overlaps(["src/a.py"], ["src/ab.py"])
    assert not _overlaps([], ["x"])


def test_review_column_cap_stops_new_cards_but_not_rework() -> None:
    board, _, _ = _board(_task("T1", "a"), _task("T2", "a"),
                         reviewers={"a": "rev"}, limits=BoardLimits(review_column_cap=1))
    board.start("a", "T1")
    assert board.submitted("T1", _done("T1")) == "review"
    board.finish_turn("a")
    assert board.next_for("a") is None                  # Review column full
    board.start("rev", "T1")
    board.reviewed("T1", "rev", _review("T1", passed=False))
    board.finish_turn("rev")
    assert board.next_for("a").task_id == "T1"          # rework is always allowed


def test_review_fail_returns_card_with_issues_then_pass_closes() -> None:
    board, ws, _ = _board(_task("T1", "a"), reviewers={"a": "rev"})
    board.start("a", "T1")
    board.submitted("T1", _done("T1"))
    board.finish_turn("a")
    assert board.next_review("rev").task_id == "T1"
    board.start("rev", "T1")
    outcome = board.reviewed("T1", "rev", _review("T1", passed=False))
    board.finish_turn("rev")
    card = board.card("T1")
    assert not outcome.passed and card.column == "in_progress" and card.review_round == 1
    assert any(e.kind == "review_issue" and "returns None" in e.text for e in board.thread("T1"))
    assert "returns None" in board.brief_for("T1")
    board.start("a", "T1")
    board.submitted("T1", _done("T1"))
    outcome = board.reviewed("T1", "rev", _review("T1", passed=True))
    assert outcome.passed and board.card("T1").column == "done" and board.all_done()
    moves = [json.loads(s.detail or "{}")["to"] for s in ws.board
             if s.type is SignalType.TASK_MOVED]
    assert moves == ["in_progress", "review", "in_progress", "review", "done"]


def test_review_rounds_exhausted_needs_attention() -> None:
    board, _, _ = _board(_task("T1", "a"), reviewers={"a": "rev"})
    for _ in range(2):
        board.start("a", "T1")
        board.submitted("T1", _done("T1"))
        board.reviewed("T1", "rev", _review("T1", passed=False))
    board.submitted("T1", _done("T1"))
    outcome = board.reviewed("T1", "rev", _review("T1", passed=False))
    assert outcome.exhausted and board.card("T1").column == "needs_attention"
    assert board.has_open("a") and board.next_for("a") is None


def test_issue_only_review_fail_is_recorded() -> None:
    board, _, _ = _board(_task("T1", "a"), reviewers={"a": "rev"})
    board.start("a", "T1")
    board.submitted("T1", _done("T1"))
    board.reviewed("T1", "rev", ReviewResult(verdict="fail", task_id="T1", issues=[
        Issue(file="api.py", detail="the error path swallows the exception silently")]))
    assert any("swallows" in e.text for e in board.thread("T1"))


def test_blocked_card_waits_behind_an_unblock_card() -> None:
    board, _, _ = _board(_task("T1", "a"), _task("T2", "b"))
    board.start("a", "T1")
    unblock = board.blocked("T1", "b", "Expose get_user() so I can call it from the client.")
    board.finish_turn("a")
    assert board.card("T1").column == "blocked" and unblock.assigned_to == "b"
    assert board.next_for("a") is None
    assert board.next_for("b").task_id == unblock.task_id   # unblock card has priority
    assert board.blocked_cycle("b", "a")
    board.start("b", unblock.task_id)
    board.submitted(unblock.task_id, AgentResult(
        status="done", task_id=unblock.task_id, summary="Exposed get_user() for the client.",
        acceptance=[AcceptanceCheck(item="Expose get_user() so I can call it from the client.",
                                    met=True, evidence="test_get_user passes")]))
    assert board.card("T1").column == "todo"
    assert board.next_for("a").task_id == "T1"


def test_reopening_a_blocked_card_settles_its_unblock_card() -> None:
    # Session 5: the overseer decided T3 need not wait for a playtest, reopened it, but the
    # unblock card (owned by a planner that had finished) stayed open and the run went stuck.
    board, _, _ = _board(_task("T1", "a"), _task("T2", "b"))
    board.start("a", "T1")
    unblock = board.blocked("T1", "b", "A human playtest that level 1 is clearable.")
    board.finish_turn("a")
    board.reopen("T1", "overseer decision")
    assert board.card(unblock.task_id).column == "done"
    assert board.card("T1").column == "todo" and board.next_for("a").task_id == "T1"
    assert board.next_for("b") is None or board.next_for("b").task_id != unblock.task_id
    assert any("settled" in e.text for e in board.thread(unblock.task_id))


def test_has_open_counts_reviewers() -> None:
    board, _, _ = _board(_task("T1", "a"), reviewers={"a": "rev"})
    assert board.has_open("rev") and board.has_open("a")
    assert not board.has_open("someone_else")


def test_aging_by_size() -> None:
    board, _, clock = _board(_task("T1", "a", size="S"), _task("T2", "a", size="M"))
    board.start("a", "T1")
    clock.now += 700
    aged = board.aging()
    assert [c.task_id for c, _ in aged] == ["T1"]          # todo cards don't age


def test_merge_ranges_and_brief() -> None:
    board, _, _ = _board(_task("T1", "a", files=["api.py"], kind="test"),
                         _task("T2", "a", deps=["T1"]))
    board.record_merge("T1", "a", "aaaaaaaa1", "bbbbbbbb2")
    board.record_merge("T1", "a", "same", "same")           # nothing merged: ignored
    assert board.merge_ranges("T1") == [("aaaaaaaa1", "bbbbbbbb2")]
    text = board.brief_for("T1")
    assert "T1 item one works" in text and "fail without the change" in text
    assert "api.py" in text and '`task_id` "T1"' in text
    assert "Cards this depends on" in board.brief_for("T2")
    assert "`items`" in board.brief_for("T1", reviewer=True)


def test_unknown_card_raises() -> None:
    board, _, _ = _board(_task("T1", "a"))
    with pytest.raises(KeyError):
        board.card("T9")


def test_cards_below_review_min_size_skip_review_with_a_note() -> None:
    board, _, _ = _board(_task("T1", "a", size="S"), _task("T2", "a", size="M"),
                         _task("T3", "a", size="L"), reviewers={"a": "rev"})
    board.review_min_size = "M"
    board.start("a", "T1")
    assert board.submitted("T1", _done("T1")) == "done"
    assert any("review skipped by size" in e["text"] for e in board.card("T1").thread)
    board.finish_turn("a")
    board.start("a", "T2")
    assert board.submitted("T2", _done("T2")) == "review"
    board.finish_turn("a")
    board.start("a", "T3")
    assert board.submitted("T3", _done("T3")) == "review"


def test_default_reviews_everything_and_visual_cards_keep_review() -> None:
    board, _, _ = _board(_task("T1", "a", size="S"), _task("T2", "a", size="S", kind="visual"),
                         reviewers={"a": "rev"})
    board.start("a", "T1")
    assert board.submitted("T1", _done("T1")) == "review"
    board.finish_turn("a")
    board.review_min_size = "L"
    board.start("a", "T2")
    assert board.submitted("T2", _done("T2")) == "review"
