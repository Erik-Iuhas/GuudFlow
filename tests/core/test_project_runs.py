"""Tests for core.project_runs helpers (plan §13, §14)."""

from __future__ import annotations

import json

from core.project_runs import card_results
from core.signals import Signal, SignalType


def _result(role: str, task: str, cost: float, summary: str = "") -> Signal:
    return Signal(type=SignalType.AGENT_RESULT, from_agent=role, summary="r",
                  detail=json.dumps({"result": {"task_id": task, "summary": summary},
                                     "cost": cost}))


def test_turn_costs_are_differences_of_the_roles_running_total() -> None:
    board = [_result("dev", "T1", 0.10, "first try"), _result("rev", "T1", 0.05),
             _result("dev", "T1", 0.25, "fixed it"), _result("dev", "T2", 0.30),
             Signal(type=SignalType.STARTED, from_agent="dev", summary="x")]
    summaries, costs = card_results(board)
    assert summaries == {"T1": "fixed it"}
    assert costs == {"T1": 0.30, "T2": 0.05}
