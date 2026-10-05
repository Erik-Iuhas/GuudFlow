"""Tests for core.backlog (plan §13): work items that outlive runs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.backlog import Backlog, BacklogError, CardOutcome, card_outcomes


def _backlog(tmp_path: Path) -> Backlog:
    return Backlog(tmp_path / "data", "p")


def test_add_orders_by_priority_and_ids_never_repeat(tmp_path: Path) -> None:
    b = _backlog(tmp_path)
    low = b.add("Write the README", ["README explains setup"], kind="docs", size="S")
    high = b.add("Add login", ["POST /login returns a token"], priority=5)
    assert [i.id for i in b.items()] == [high.id, low.id]
    assert (low.id, high.id) == ("B1", "B2")
    b.drop(low.id, "not needed")
    assert b.add("Third", []).id == "B3"
    assert b.get("B1").status == "dropped" and b.get("B1").history[-1]["decision"] == "not needed"


@pytest.mark.parametrize("change", [{"title": "x"}, {"kind": "art"}, {"size": "XL"},
                                    {"priority": "high"}, {"acceptance": "one"},
                                    {"status": "done"}])
def test_bad_edits_are_refused(tmp_path: Path, change: dict[str, object]) -> None:
    b = _backlog(tmp_path)
    item = b.add("Add login", ["works"])
    with pytest.raises(BacklogError):
        b.update(item.id, **change)


def test_a_run_takes_the_top_open_items(tmp_path: Path) -> None:
    b = _backlog(tmp_path)
    for i, title in enumerate(["Login", "Logout", "Profile page", "Settings"]):
        b.add(title, ["works"], priority=i)
    taken = b.take_for_run("run_1", limit=2)
    assert [t.title for t in taken] == ["Settings", "Profile page"]
    assert {i.id for i in b.in_run("run_1")} == {t.id for t in taken}
    with pytest.raises(BacklogError):
        b.take_for_run("run_2", ids=[taken[0].id])          # already in a run
    with pytest.raises(BacklogError):
        b.drop(taken[0].id)                                 # cannot drop while in a run


def test_accepted_run_closes_finished_items_and_reopens_the_rest(tmp_path: Path) -> None:
    b = _backlog(tmp_path)
    login = b.add("Login", ["POST /login works"])
    logout = b.add("Logout", ["POST /logout works"])
    b.take_for_run("run_1", ids=[login.id, logout.id])
    created = b.record_outcomes("run_1", [
        CardOutcome("Login endpoint", "done", login.id, summary="added /login"),
        CardOutcome("Login tests", "done", login.id),
        CardOutcome("Logout endpoint", "review", logout.id),
        CardOutcome("Rate limiting", "todo", None, ("429 after 5 tries",)),
        CardOutcome("Refactor utils", "done", None),        # done planner extras vanish
    ])
    assert [c.title for c in created] == ["Rate limiting"]
    assert created[0].source == "run:run_1" and created[0].acceptance == ["429 after 5 tries"]
    assert b.get(login.id).pending == "done" and b.get(logout.id).pending == "not_started"
    assert b.get(login.id).status == "in_run"               # not done until accepted
    b.decide("run_1", accepted=True)
    assert b.get(login.id).status == "done"
    assert b.get(logout.id).status == "open" and b.get(logout.id).run_id is None
    assert b.get(login.id).history[-1]["decision"] == "accepted"
    assert b.get(login.id).history[-1]["summary"] == "added /login"


def test_rejected_run_reopens_everything(tmp_path: Path) -> None:
    b = _backlog(tmp_path)
    item = b.add("Login", ["works"])
    b.take_for_run("run_1", ids=[item.id])
    b.record_outcomes("run_1", [CardOutcome("Login", "done", item.id, summary="did it")])
    b.decide("run_1", accepted=False)
    again = b.get(item.id)
    assert again.status == "open" and again.history[-1]["decision"] == "rejected"
    assert again.last_summary() == "run run_1: did it"         # the next run learns from it


def test_release_and_duplicate_leftovers(tmp_path: Path) -> None:
    b = _backlog(tmp_path)
    item = b.add("Login", ["works"])
    b.add("Rate limiting", [])
    b.take_for_run("run_1", ids=[item.id])
    created = b.record_outcomes("run_1", [CardOutcome("rate limiting", "todo")])
    assert created == []                                        # already on the backlog
    b.release("run_1", "run failed")
    assert b.get(item.id).status == "open"


def test_card_outcomes_reads_board_cards() -> None:
    cards = [SimpleNamespace(task_id="T1", story_id="plan", title="Login", column="done",
                             backlog_id="B1", acceptance_criteria=["a"], kind="code"),
             SimpleNamespace(task_id="X", story_id="legacy", title="old", column="done")]
    out = card_outcomes(cards, {"T1": "added login"})
    assert out == [CardOutcome("Login", "done", "B1", ("a",), "code", "added login")]


def test_the_reviewers_note_reaches_the_next_run(tmp_path: Path) -> None:
    from core.harness.task_flow import backlog_text
    b = _backlog(tmp_path)
    item = b.add("Login", ["works"])
    b.take_for_run("run_1", ids=[item.id])
    b.record_outcomes("run_1", [CardOutcome("Login", "done", item.id, summary="did it")])
    b.decide("run_1", accepted=False, note="Use bcrypt, not sha1.")
    again = b.get(item.id)
    assert again.review_note() == "when rejected run run_1: Use bcrypt, not sha1."
    text = backlog_text([again])
    assert "the human reviewer said when rejected run run_1: Use bcrypt, not sha1." in text
    assert text.index("Use bcrypt") < text.index("<untrusted-")   # human note is not wrapped
