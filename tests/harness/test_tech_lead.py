"""The tech lead plans sessions against the design (role contracts plan §2.2, §4.2, §8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.design import Design, DesignDrafts, DesignTurn
from core.harness.results import AcceptanceCheck, AgentResult
from core.harness.task_flow import TaskFlow
from core.harness.tasks import PlannedTask, PlanResult
from core.runner import AgentConfig
from core.signals import SignalType
from core.work_db import WorkDB
from core.workspace import Workspace

WHY = {"name": "Dash owner", "summary": "The default dash gets one owning component.",
       "because": "Two abilities started their own dash.", "evidence": ["web/src/logic"]}
LOGIC = {"op": "add_component", "ref": "new:1", "name": "Game logic", "group": "Dash owner",
         "paths": ["web/src/logic/**"], "responsibility": "Rules of play, pure and deterministic."}
DASH = {"op": "add_component", "ref": "new:2", "name": "Dash", "group": "Dash owner",
        "paths": ["web/src/logic/dash/**"], "responsibility": "The one default dash and its charges."}
START = {"op": "add_interface", "ref": "new:3", "name": "startDash", "provider": "new:2",
         "signature": "startDash(state, input) -> boolean", "users": ["new:1"],
         "group": "Dash owner"}


class FakeScheduler:
    max_iterations = 12

    def is_running(self, role: str) -> bool:
        return False

    def rearm(self, role: str, payload: object = None) -> None:
        pass

    def notify(self) -> None:
        pass


def _flow(tmp_path: Path, *, auto: bool = True, adopt: bool = False,
          posted: list | None = None) -> TaskFlow:
    (tmp_path / "projects" / "p1").mkdir(parents=True, exist_ok=True)
    ws = Workspace(run_id="r", goal="g")
    agents = [AgentConfig(role="lead", role_type="architect", contract="tech_lead"),
              AgentConfig(role="logic", depends_on=["lead"]),
              AgentConfig(role="rev", is_reviewer=True, depends_on=["logic"],
                          review_of=["logic"])]
    types = {"lead": "architect", "logic": "builder", "rev": "reviewer"}
    sink = posted if posted is not None else []
    flow = TaskFlow.build(agents, types, ws, lambda: FakeScheduler(),  # type: ignore[arg-type,return-value]
                          post=lambda k, r, s, d: sink.append((k, s, d)),
                          escalate=lambda r, s, d: None, planner_role="lead")
    assert flow is not None and flow.planner == "lead"
    flow.designer, flow.design_auto, flow.adopt = True, auto, adopt
    flow.data_root, flow.project_id, flow.run_id = tmp_path, "p1", "r"
    flow.design = Design.load(tmp_path, "p1")
    return flow


def _task(tid: str = "T1", **kw: Any) -> PlannedTask:
    return PlannedTask(id=tid, title="Default dash", owner_role="logic", size="M", kind="code",
                       acceptance=["the dash spends a charge"], **kw)


def _sections(turn: object) -> dict[str, str]:
    return dict(turn.extra_sections)  # type: ignore[attr-defined]


def test_tech_lead_turn_has_design_sections_and_checks(tmp_path: Path) -> None:
    flow = _flow(tmp_path)
    turn = flow.begin_turn("lead")
    assert turn is not None and turn.model is PlanResult
    sections = _sections(turn)
    assert sections["The design"] == "There is no design yet."
    assert "You own the design" in sections["Your design work"]
    # With design changes in the plan, every card names what it builds.
    plan = PlanResult(tasks=[_task()], design=DesignTurn.model_validate(
        {"groups": [WHY], "changes": [LOGIC, DASH, START]}))
    problems = turn.check(plan)
    assert any("name the design element it builds" in p for p in problems)
    plan.tasks[0].implements = ["new:3"]
    assert turn.check(plan) == []
    plan.tasks[0].implements = ["I9"]
    assert any("implements I9" in p for p in turn.check(plan))
    # A plan with no tasks needs a reason to exist.
    assert any("no tasks" in p for p in turn.check(PlanResult()))


def test_plan_applies_design_and_resolves_cards(tmp_path: Path) -> None:
    posted: list = []
    flow = _flow(tmp_path, posted=posted)
    plan = PlanResult(tasks=[_task(implements=["new:3"])], design=DesignTurn.model_validate(
        {"groups": [WHY], "changes": [LOGIC, DASH, START]}))
    flow.plan_accepted(plan)
    card = flow.board.card("T1")
    assert card.implements == ["I1"]
    assert card.files_hint == ["web/src/logic/dash/**"]         # from the owning component
    design = Design.load(tmp_path, "p1")
    assert [c.name for c in design.components()] == ["Game logic", "Dash"]
    updates = [p for p in posted if p[0] is SignalType.DESIGN_UPDATED]
    assert len(updates) == 1 and "applied" in updates[0][1]
    # The builder's brief shows the part of the design its card builds.
    turn = flow.begin_turn("logic")
    assert turn is not None
    text = _sections(turn)["The design"]
    assert "C2 Dash" in text and "startDash" in text and "C1 Game logic" not in text
    flow.board.submitted("T1", AgentResult(
        status="done", task_id="T1", summary="Built the default dash.",
        acceptance=[AcceptanceCheck(item="the dash spends a charge", met=True,
                                    evidence="bun test passes")]))
    review = flow.begin_turn("rev")
    assert review is not None
    assert "design-I1" in review.task_text or "design-I1" in "\n".join(
        s for _, s in review.extra_sections)


def test_human_lead_drafts_the_design(tmp_path: Path) -> None:
    posted: list = []
    flow = _flow(tmp_path, auto=False, posted=posted)
    flow.plan_accepted(PlanResult(tasks=[_task(implements=["new:3"])],
                                  design=DesignTurn.model_validate(
                                      {"groups": [WHY], "changes": [LOGIC, DASH, START]})))
    assert Design.load(tmp_path, "p1").empty
    assert [d["status"] for d in DesignDrafts(tmp_path, "p1").all()] == ["open"]
    assert flow.board.card("T1").implements == ["new:3"]
    assert "wait for the Lead seat" in [p for p in posted
                                        if p[0] is SignalType.DESIGN_UPDATED][0][1]


def test_adopt_recovers_the_design_without_cards(tmp_path: Path) -> None:
    flow = _flow(tmp_path, adopt=True)
    turn = flow.begin_turn("lead")
    assert turn is not None
    assert "recovers it" in _sections(turn)["Your design work"]
    plan = PlanResult(design=DesignTurn.model_validate({"groups": [WHY], "changes": [LOGIC]}))
    assert turn.check(plan) == []
    flow.plan_accepted(plan)
    assert [c.id for c in Design.load(tmp_path, "p1").components()] == ["C1"]
    assert flow.board.cards() == []


def test_feasibility_notes_reach_the_plan_conversation(tmp_path: Path) -> None:
    flow = _flow(tmp_path)
    flow.plan_accepted(PlanResult(tasks=[_task()], spec_notes=[
        "R6.9 says Dark draws only around Pac-Man, which the readability rule forbids."]))
    rows = WorkDB(tmp_path / "projects" / "p1").list_plan_messages()
    assert rows[-1]["author"] == "tech_lead" and "R6.9" in rows[-1]["text"]


def test_drift_is_noted_on_the_card(tmp_path: Path) -> None:
    posted: list = []
    flow = _flow(tmp_path, posted=posted)
    flow.plan_accepted(PlanResult(tasks=[_task(implements=["new:3"])],
                                  design=DesignTurn.model_validate(
                                      {"groups": [WHY], "changes": [LOGIC, DASH, START]})))
    flow.exports_for = lambda base, head: {"web/src/logic/dash/dash.js": ["startDash(s, i)",
                                                                         "secretHelper()"]}
    flow._check_drift("logic", "T1", "a1", "b2")
    note = [e for e in flow.board.card("T1").thread if e.get("data", {}).get("drift")][0]
    assert "secretHelper" in note["text"] and "startDash" not in note["text"]
    assert [p for p in posted if p[0] is SignalType.DESIGN_DRIFT]


def test_knowledge_reaches_lead_and_cards(tmp_path: Path) -> None:
    from core.knowledge import KnowledgeNote, notes
    flow = _flow(tmp_path)
    flow.notebooks = {"logic": "Ghost tests need mode set first."}
    turn = flow.begin_turn("lead")
    assert turn is not None
    lead_view = _sections(turn)["The knowledge base"]
    assert "notebook of logic" in lead_view and "Promote what more than one role needs" in lead_view
    flow.facts = {"modules": [{"path": "web/src/logic/dash/dash.js", "lines": 40,
                               "exports": ["startDash(s, i)"]}], "commands": []}
    flow.plan_accepted(PlanResult(
        tasks=[_task(files_hint=["web/src/logic/dash/**"])],
        knowledge=[KnowledgeNote(text="The dash refills on every power pellet, all charges.",
                                 topic="Dash", paths=["web/src/logic/dash/**"])]))
    assert [n["approved_by"] for n in notes(tmp_path, "p1")] == ["lead"]
    card_turn = flow.begin_turn("logic")
    assert card_turn is not None
    text = _sections(card_turn)["Project knowledge"]
    assert "dash.js` (40 lines)" in text and "refills on every power pellet" in text


def test_adopt_links_existing_tests_to_checks(tmp_path: Path) -> None:
    from core.harness.spec_brief import SpecBrief
    from core.spec_adherence import run_check_links
    from tests.spec_fixtures import seed_spec
    seed_spec(tmp_path)
    flow = _flow(tmp_path, adopt=True)
    flow.spec = SpecBrief.load(tmp_path, "p1", "adopt")
    flow.facts = {"tests": ["tests/list.test.js"], "modules": [], "commands": []}
    turn = flow.begin_turn("lead")
    assert turn is not None
    unlinked = _sections(turn)["Checks with no test yet"]
    assert "R1.1" in unlinked and "check_links" in unlinked
    bad = PlanResult(design=DesignTurn.model_validate({"groups": [WHY], "changes": [LOGIC]}),
                     check_links=[{"check": "R1.1", "test": "tests/ghost.test.js"}])
    assert any("names no test file" in p for p in turn.check(bad))
    good = PlanResult(design=DesignTurn.model_validate({"groups": [WHY], "changes": [LOGIC]}),
                      check_links=[{"check": "R1.1", "test": "tests/list.test.js::shows saved"}])
    assert turn.check(good) == []
    flow.plan_accepted(good)
    assert run_check_links(tmp_path, "r") == {"R1.1": "tests/list.test.js::shows saved"}
