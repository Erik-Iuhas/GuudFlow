"""Quips as a work simulator (plan §11.5): office state, review subject, new events."""

from __future__ import annotations

import json
from pathlib import Path

from core.llm import MockLLMClient, MockTurn
from core.quip_engine import AgentVoice, QuipEngine, QuipPump
from core.quip_office import interactions, office_state, standing
from core.signals import Signal, SignalType
from core.workspace import Task, Workspace

NAMES = {"logic": "Sol", "render": "Lena", "rev": "Bram"}


def _ws() -> Workspace:
    ws = Workspace(run_id="r", goal="g")
    for tid, owner, column, rounds in (("T1", "logic", "done", 0), ("T2", "logic", "done", 1),
                                       ("T3", "render", "in_progress", 0)):
        task = Task(task_id=tid, story_id="plan", assigned_to=owner, title=f"card {tid}",
                    goal=f"card {tid}", created_by="planner")
        task.column, task.review_round = column, rounds
        ws.tasks[tid] = task
    ws.post_signal(Signal(type=SignalType.AGENT_RESULT, from_agent="logic", summary="x",
                          detail=json.dumps({"cost": 3.8})))
    ws.post_signal(Signal(type=SignalType.AGENT_RESULT, from_agent="render", summary="x",
                          detail=json.dumps({"cost": 2.7})))
    ws.post_signal(Signal(type=SignalType.REJECTED, from_agent="rev", summary="x",
                          detail=json.dumps({"task_id": "T2", "authors": ["logic"], "round": 1})))
    return ws


def test_standing_counts_cards_first_tries_send_backs_and_spend() -> None:
    lines = standing(_ws(), NAMES, ["logic", "render"])
    assert lines[0] == ("- Sol (logic): 2 of 2 card(s) done, 1 passed review first try, "
                        "sent back 1 time(s), $3.80 spent (the most so far)")
    assert lines[1] == "- Lena (render): 0 of 1 card(s) done, $2.70 spent"


def test_interactions_are_about_the_speaker() -> None:
    ws = _ws()
    assert interactions(ws, NAMES, "logic") == ["Bram sent T2 back to Sol (round 1)"]
    assert interactions(ws, NAMES, "render") == []
    ws.post_signal(Signal(type=SignalType.RUN_LIMITS_CHANGED, summary="run limits changed: budget_usd=15.0"))
    assert interactions(ws, NAMES, "render") == ["run limits changed: budget_usd=15.0"]


def test_office_state_puts_the_speaker_first() -> None:
    text = office_state(_ws(), NAMES, "render", ["logic", "render", "rev"])
    assert text.index("Lena (render)") < text.index("Sol (logic)")
    assert "Where everyone stands" in text


def _engine(tmp_path: Path, *texts: str) -> tuple[QuipEngine, MockLLMClient]:
    llm = MockLLMClient([MockTurn(text=t) for t in texts])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    engine.set_voices([AgentVoice(role=r, name=n, character="A coworker.") for r, n in NAMES.items()]
                      + [AgentVoice(role="overseer", name="Ola", character="Watches.")])
    return engine, llm


async def test_review_quip_names_the_card_and_its_builder(tmp_path: Path) -> None:
    engine, llm = _engine(tmp_path, "clean work")
    await engine.on_review_verdict("rev", "APPROVED", _ws(), task_id="T1")
    prompt = llm.calls[0]["messages"][0].content
    assert "T1, Sol's card \"card T1\"" in prompt and "overseer" not in prompt


async def test_prompt_carries_office_state_recent_quips_and_banned_openers(tmp_path: Path) -> None:
    engine, llm = _engine(tmp_path, "first", "second")
    ws = _ws()
    await engine.on_agent_started("logic", ws)
    await engine.on_agent_started("logic", ws)
    system = llm.calls[1]["system"]
    assert "Sol (logic): 2 of 2 card(s) done" in system
    assert "Bram sent T2 back to Sol" in system
    assert "rolling up my sleeves" in system and "never question anyone's competence" in system
    assert "say something new:\n- first" in system


async def test_bridge_maps_run_level_events_and_review_card_ids(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    bridge = QuipPump(engine, _ws())
    bridge.on_signal(Signal(type=SignalType.BUDGET_WARNING, from_agent="system", summary="$8.40 of $10"))
    bridge.on_signal(Signal(type=SignalType.AGENT_RESULT, from_agent="rev",
                            summary="reviewer: review pass (0 issue(s))",
                            detail=json.dumps({"result": {"task_id": "T1"}})))
    bridge.on_signal(Signal(type=SignalType.REJECTED, from_agent="rev", summary="x",
                            detail=json.dumps({"task_id": "T2", "authors": ["logic"]})))
    queued = [bridge._queue.get_nowait() for _ in range(3)]   # noqa: SLF001
    assert queued == [("", "budget_warning", "$8.40 of $10"), ("rev", "APPROVED", "T1"),
                      ("rev", "REJECTED", "T2")]


async def test_random_speaker_is_never_the_overseer(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    assert {engine._random_voice().role for _ in range(40)} <= {"logic", "render", "rev"}  # noqa: SLF001
