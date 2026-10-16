"""The tech lead before the run (worker context plan H): prepare, revise, answer, approve."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.backlog import Backlog
from core.llm.client import MockLLMClient, MockTurn, ToolCall
from core.plan_drafts import AddDecision, PlanDrafts
from core.preflight import RUN_PLAN_JSON, TOOL_NAME, Preflight, PreflightError, run_plan
from core.spec import Spec

TEAM = [
    {"role": "tech_lead", "role_type": "architect", "contract": "tech_lead"},
    {"role": "dev", "role_type": "builder", "depends_on": ["tech_lead"]},
    {"role": "reviewer", "role_type": "reviewer", "depends_on": ["dev"], "review_of": ["dev"]},
]
DOC = {"project_id": "p1", "goal": "A tiny game", "agents": TEAM}


def _turn(tasks: list[dict[str, Any]], assumptions: list[dict[str, Any]] | None = None,
          reply: str = "Here is the plan.") -> MockTurn:
    return MockTurn(tool_calls=[ToolCall("1", TOOL_NAME, {
        "reply": reply, "plan": {"tasks": tasks, "notes": ""},
        "assumptions": assumptions or []})])


def _card(tid: str = "T1", owner: str = "dev", backlog_id: str | None = "B1",
          title: str = "Draw the board") -> dict[str, Any]:
    card: dict[str, Any] = {"id": tid, "title": title, "owner_role": owner,
                            "acceptance": ["the board shows 8x8 squares"], "size": "S",
                            "kind": "code"}
    if backlog_id:
        card["backlog_id"] = backlog_id
    return card


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "projects" / "p1").mkdir(parents=True)
    Backlog(tmp_path, "p1").add("Draw the board", ["the board shows"])
    return tmp_path


async def test_prepare_validates_like_the_planner_and_repairs(root: Path) -> None:
    llm = MockLLMClient([
        _turn([_card(owner="ghost")]),                       # not a card owner: rejected
        _turn([_card()], [{"text": "Use pygame for drawing", "decided_by": "tech_lead",
                           "choice": "pygame"},
                          {"text": "How big should the window be?", "decided_by": "owner"}]),
    ])
    pf = Preflight(root, "p1", DOC)
    row = await pf.prepare(llm, ["B1"], "first slice")
    assert len(llm.calls) == 2
    assert "ghost" in llm.calls[1]["messages"][0].content      # the problem went back
    assert row["status"] == "draft" and row["problems"] == []
    assert [t["id"] for t in row["plan"]["tasks"]] == ["T1"]
    assert row["open_questions"] == ["A2"]
    assert row["messages"][-1]["author"] == "tech_lead"
    # The same sections as the in-run planner, and the write-cards skill in the system prompt.
    assert "This run's backlog items" in llm.calls[0]["messages"][0].content
    assert "How to write cards" in llm.calls[0]["system"]


async def test_owner_questions_block_approval_and_answers_become_decisions(root: Path) -> None:
    llm = MockLLMClient([_turn([_card()], [{"text": "How big should the window be?",
                                            "decided_by": "owner"}])])
    pf = Preflight(root, "p1", DOC)
    await pf.prepare(llm, ["B1"])
    with pytest.raises(PreflightError, match="A1"):
        pf.approve()
    row = pf.answer("A1", "800 by 600")
    assert row["open_questions"] == []
    spec = Spec(PlanDrafts(root, "p1").db.read_spec_doc())
    assert any(d.choice == "800 by 600" for d in spec.decisions)
    row = pf.approve()
    assert row["status"] == "approved" and row["stale"] == []


async def test_edit_cards_runs_the_plan_checks(root: Path) -> None:
    pf = Preflight(root, "p1", DOC)
    await pf.prepare(MockLLMClient([_turn([_card()])]), ["B1"])
    with pytest.raises(PreflightError, match="ghost"):
        pf.edit_cards([_card(owner="ghost")])
    row = pf.edit_cards([_card(), _card("T2", title="Move the pieces", backlog_id="B1")])
    assert [t["id"] for t in row["plan"]["tasks"]] == ["T1", "T2"]


async def test_hand_to_run_and_staleness(root: Path, tmp_path: Path) -> None:
    pf = Preflight(root, "p1", DOC)
    await pf.prepare(MockLLMClient([_turn([_card()])]), ["B1"])
    row = pf.approve()
    PlanDrafts(root, "p1").apply_direct(
        AddDecision(op="add_decision", ref="new:1", title="Storage", choice="files",
                    reason="nothing to install for the owner"),
        "human")
    assert any("spec changed" in s for s in pf.current()["stale"])
    run_root = tmp_path / "run"
    run_root.mkdir()
    assert not pf.hand_to_run("P9", "run_x", run_root)
    assert pf.hand_to_run(row["id"], "run_x", run_root)
    assert (run_root / RUN_PLAN_JSON).is_file()
    plan, pid = run_plan(run_root)
    assert pid == row["id"] and [t.id for t in plan.tasks] == ["T1"]
    assert pf.current() is None                                  # used: nothing open


async def test_no_planner_is_refused(root: Path) -> None:
    pf = Preflight(root, "p1", {"agents": [{"role": "dev", "role_type": "builder"}]})
    with pytest.raises(PreflightError, match="no tech lead"):
        await pf.prepare(MockLLMClient([]), ["B1"])
