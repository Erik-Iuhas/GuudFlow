"""Tests for core.harness.detectors (plan §11.2, O18): one fire + one quiet case each."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.harness.detectors import (
    DetectorConfig,
    RunView,
    Stall,
    StallWatch,
    blocked_chain,
    burn_without_output,
    card_aging,
    detect,
    escalation,
    needs_attention,
    no_progress,
    permission_thrash,
    same_review_issue,
    situation_report,
    validation_failures,
)
from core.signals import Signal, SignalType
from core.workspace import Task

CFG = DetectorConfig()
NOW = 10_000.0


def _sig(kind: SignalType, role: str = "a", summary: str = "s", detail: str | None = None,
         at: float = NOW) -> Signal:
    sig = Signal(type=kind, summary=summary, from_agent=role, detail=detail)
    sig.timestamp = datetime.fromtimestamp(at, tz=timezone.utc)
    return sig


def _card(tid: str, owner: str = "a", column: str = "todo", **kw: object) -> Task:
    return Task(task_id=tid, story_id="plan", assigned_to=owner, title=f"card {tid}",
                column=column, **kw)  # type: ignore[arg-type]


def _view(signals: list[Signal] | None = None, cards: list[Task] | None = None,
          **kw: object) -> RunView:
    defaults: dict[str, object] = {"role_states": {"a": "running"}, "running": ["a"],
                                   "now": NOW, "started_at": NOW - 60}
    defaults.update(kw)
    return RunView(signals=signals or [], cards=cards or [], **defaults)  # type: ignore[arg-type]


def test_no_progress() -> None:
    stale = _view(started_at=NOW - 3600, signals=[_sig(SignalType.COMPLETE, at=NOW - 900)])
    assert [s.detector for s in no_progress(stale, CFG)] == ["no_progress"]
    fresh = _view(started_at=NOW - 3600, signals=[_sig(SignalType.GIT_MERGE, at=NOW - 60)])
    assert no_progress(fresh, CFG) == []
    idle = _view(started_at=NOW - 3600, role_states={"a": "complete"}, running=[])
    assert no_progress(idle, CFG) == []


def test_same_review_issue_across_rounds() -> None:
    def entries(rounds: int) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for _ in range(rounds):
            out.append({"kind": "note", "text": "merged", "data": {"merge": ["a", "b"]}})
            out.append({"kind": "review_issue", "text": "x", "data": {"item": "Status is OK"}})
        return out
    repeated = _card("T1", thread=entries(2))
    once = _card("T2", thread=entries(1))
    found = same_review_issue(_view(cards=[repeated, once]), CFG)
    assert [(s.detector, s.subject) for s in found] == [("same_review_issue", "T1")]


def test_blocked_chain_and_cycle() -> None:
    cycle = [_card("T1", "a", "blocked", blocked_on=["b"]),
             _card("T2", "b", "blocked", blocked_on=["a"])]
    assert any("cycle" in s.detail for s in blocked_chain(_view(cards=cycle), CFG))
    chain = [_card("T1", "a", "blocked", blocked_on=["b"]),
             _card("T2", "b", "blocked", blocked_on=["c"]),
             _card("T3", "c", "blocked", blocked_on=["d"])]
    assert any("chain" in s.detail for s in blocked_chain(_view(cards=chain), CFG))
    short = [_card("T1", "a", "blocked", blocked_on=["b"])]
    assert blocked_chain(_view(cards=short), CFG) == []


def test_a_blocked_card_with_nothing_running_wakes_the_overseer() -> None:
    # Session 5: T3 waited on the planner, which had finished; with nothing running the run
    # ended stuck and the overseer never woke.
    cards = [_card("T3", "render", "blocked", blocked_on=["planner"]), _card("T4", "render")]
    found = blocked_chain(_view(cards=cards, running=[], role_states={"render": "ready"}), CFG)
    assert [(s.detector, s.subject) for s in found] == [("blocked_chain", "render")]
    assert "T3 (render) waits on planner" in found[0].detail
    assert blocked_chain(_view(cards=cards), CFG) == []   # someone is still working


def test_a_card_blocked_on_a_finished_role_wakes_the_overseer_even_while_others_work() -> None:
    cards = [_card("T3", "render", "blocked", blocked_on=["planner"]), _card("T4", "logic", "in_progress")]
    view = _view(cards=cards, running=["logic"],
                 role_states={"planner": "complete", "render": "ready", "logic": "running"})
    found = blocked_chain(view, CFG)
    assert [(s.detector, s.subject) for s in found] == [("blocked_chain", "render")]
    assert "finished for this run" in found[0].detail
    busy = _view(cards=cards, running=["logic", "planner"],
                 role_states={"planner": "running", "render": "ready", "logic": "running"})
    assert blocked_chain(busy, CFG) == []


def test_burn_without_output() -> None:
    cards = [_card("T1", column="done"), _card("T2"), _card("T3")]
    assert burn_without_output(_view(cards=cards, spent_usd=3.0, budget_usd=5.0), CFG)
    assert not burn_without_output(_view(cards=cards, spent_usd=1.0, budget_usd=5.0), CFG)
    assert not burn_without_output(_view(cards=cards, spent_usd=9.0, budget_usd=None), CFG)


def test_permission_thrash() -> None:
    signals = []
    for i in range(3):
        signals.append(_sig(SignalType.APPROVAL_NEEDED, "a", detail=f"approval_id=ap{i}; kind=x"))
        signals.append(_sig(SignalType.APPROVAL_DENIED, "human", detail=f"approval_id=ap{i}"))
    assert [s.subject for s in permission_thrash(_view(signals), CFG)] == ["a"]
    assert permission_thrash(_view(signals[:4]), CFG) == []


def test_validation_failures_streak_resets_on_result() -> None:
    fail = _sig(SignalType.FAILED, "a", "a finished without a valid result")
    ok = _sig(SignalType.AGENT_RESULT, "a")
    assert validation_failures(_view([fail, fail]), CFG)
    assert validation_failures(_view([fail, ok, fail]), CFG) == []


def test_needs_attention_and_escalation() -> None:
    card = _card("T1", column="needs_attention", thread=[{"kind": "note", "text": "rounds out"}])
    assert "rounds out" in needs_attention(_view(cards=[card]), CFG)[0].detail
    assert needs_attention(_view(cards=[_card("T2")]), CFG) == []
    esc = _sig(SignalType.ESCALATION, "reviewer", "review rounds exhausted")
    assert escalation(_view([esc]), CFG)[0].detector == "escalation"


def test_card_aging() -> None:
    old = _card("T1", column="in_progress", size="S", column_since=NOW - 700)
    young = _card("T2", column="in_progress", size="M", column_since=NOW - 700)
    todo = _card("T3", column="todo", size="S", column_since=NOW - 7000)
    assert [s.subject for s in card_aging(_view(cards=[old, young, todo]), CFG)] == ["T1"]


def test_detect_runs_everything() -> None:
    card = _card("T1", column="needs_attention")
    assert {s.detector for s in detect(_view(cards=[card]))} == {"needs_attention"}


def test_stall_watch_dedupes_cools_down_and_caps() -> None:
    watch = StallWatch(cooldown_seconds=100, max_wakes=2)
    stall = Stall("needs_attention", "T1", "x")
    assert watch.offer([stall], now=0) == [stall]
    assert watch.offer([stall], now=10) == []              # already pending
    assert watch.take() == [stall] and watch.wakes == 1
    assert watch.offer([stall], now=50) == []              # cooling down
    assert watch.offer([stall], now=200) == [stall]
    watch.take()
    assert watch.exhausted


def test_situation_report_mentions_everything() -> None:
    card = _card("T1", "a", "in_progress", review_round=1,
                 thread=[{"kind": "review_issue", "text": "returns None"}])
    report = situation_report(
        _view([_sig(SignalType.WEB_REQUEST)], [card], spent_usd=1.5, budget_usd=5.0),
        [Stall("same_review_issue", "T1", "T1 failed again on: status")], {"a": "a: done — x"})
    for text in ("same_review_issue", "T1 [in_progress]", "returns None", "$1.50 of $5.00",
                 "web requests 1", "last result: a: done"):
        assert text in report, text
    assert json.dumps(report)  # plain text, serialisable


# ── time_limit (plan §11.6) ─────────────────────────────────────────────────

def _timed(used_minutes: float, mode: str = "soft") -> RunView:
    start = NOW - used_minutes * 60
    return RunView(signals=[], cards=[_card("T1"), _card("T2", column="done")], role_states={},
                   running=["a"], now=NOW, started_at=start, deadline_at=start + 60 * 60,
                   deadline_mode=mode)


def test_time_limit_warns_at_80_percent_then_fires_at_the_limit() -> None:
    from core.harness.detectors import time_limit
    assert time_limit(_timed(30), CFG) == []
    warn = time_limit(_timed(50), CFG)
    assert [s.subject for s in warn] == ["warning"] and "50 of 60 minutes" in warn[0].detail
    reached = time_limit(_timed(61), CFG)
    assert [s.subject for s in reached] == ["reached"]
    assert "1 card(s) not done (T1)" in reached[0].detail and "running: a" in reached[0].detail
    assert time_limit(_timed(61, mode="hard"), CFG) == []


def test_time_limit_fires_once_and_does_not_use_up_wakes() -> None:
    watch = StallWatch(max_wakes=1)
    stall = Stall("time_limit", "warning", "x")
    assert watch.offer([stall], now=NOW) == [stall]
    watch.take()
    assert watch.wakes == 0                                   # a free wake
    assert watch.offer([stall], now=NOW + 10_000) == []       # never again
    watch.offer([Stall("no_progress", "", "y")], now=NOW)
    watch.take()
    assert watch.exhausted                                    # the one counted wake is used
    watch.offer([Stall("time_limit", "reached", "z")], now=NOW)
    assert watch.free_only                                    # the cap does not block it
