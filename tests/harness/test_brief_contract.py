"""What every worker is told (worker context plan A4): the regression net for F1-F8.

From one fixture spec, render the rules file and the brief of every role (tech lead, builder,
tester, reviewer, and a builder in a team with no task board) and check each holds the stack,
every binding rule that applies to it, its card's check lines and its orientation."""

from __future__ import annotations

from pathlib import Path

from core.harness.brief import BriefInputs, build_brief
from core.harness.spec_brief import SpecBrief
from core.harness.worker_rules import rules_text, skills_for, where_you_are
from core.work_db import WorkDB
from tests.harness.test_spec_brief import R11, R12, _flow, _item, _plan, _to_review
from tests.spec_fixtures import RULE1, RULE2, seed_spec

STACK = {"language": "Python 3.12", "framework": "pygame 2.6",
         "test_command": "python -m pytest -q", "test_dir": "tests"}


def _project(tmp_path: Path) -> SpecBrief:
    seed_spec(tmp_path)
    with WorkDB(tmp_path / "projects" / "p1").spec_doc() as doc:
        doc["stack"] = STACK
    brief = SpecBrief.load(tmp_path, "p1", None)
    assert brief is not None
    return brief


def _rules(brief: SpecBrief, role: str, role_type: str, work: Path, *, planner: bool = False
           ) -> str:
    return rules_text(
        role=role, name=role.title(), role_type=role_type, planner=planner,
        ground_rules=brief.ground_rules("*" if planner else role_type),
        where=where_you_are(work, stack=brief.spec.stack, sandbox=True),
        skills=skills_for(role_type, brief.spec.stack))


def _told(rules: str, plan: object, goal: str = "Build the recipe game") -> str:
    """Everything the worker reads: its rules file plus its brief."""
    sections = tuple(getattr(plan, "extra_sections", ()))
    task = getattr(plan, "task_text", None)
    return rules + "\n" + build_brief(BriefInputs(goal=goal, role="x", description="d",
                                                  task=task, extra_sections=sections))


def test_every_role_is_told_the_stack_its_rules_its_checks_and_where_it_is(tmp_path: Path) -> None:
    brief = _project(tmp_path)
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "requirements.txt").write_text("pygame==2.6.1\n")
    flow, _ = _flow(brief, [_item()])

    lead = _told(_rules(brief, "planner", "planner", work, planner=True), flow.begin_turn("planner"))
    flow.plan_accepted(_plan("api", acceptance=["the list screen renders"]))
    builder = _told(_rules(brief, "api", "builder", work), flow.begin_turn("api"))
    flow2, _ = _flow(brief, [_item()])
    flow2.plan_accepted(_plan("tester", acceptance=[R11, R12]))
    tester = _told(_rules(brief, "tester", "tester", work), flow2.begin_turn("tester"))
    flow3, _ = _flow(brief, [_item()])
    flow3.plan_accepted(_plan("api"))
    _to_review(flow3)
    reviewer = _told(_rules(brief, "reviewer", "reviewer", work), flow3.begin_turn("reviewer"))
    plain = _told(_rules(brief, "dev", "builder", work), None)          # no task board

    for name, told in {"tech lead": lead, "builder": builder, "tester": tester,
                       "reviewer": reviewer, "plain builder": plain}.items():
        assert "Python 3.12" in told and "pygame 2.6" in told, f"{name}: the stack"
        assert RULE1 in told, f"{name}: rule-1 (binding, for everyone)"
        assert "## Where you are" in told and "`tests/`" in told, f"{name}: orientation"
        assert "`python -m pytest -q`" in told, f"{name}: the check command"
        assert ".guudflow/project/SPEC.md" in told, f"{name}: where the spec is"
        assert "SQLite" in told, f"{name}: the binding decision"
    assert RULE2 in tester and RULE2 in lead and RULE2 not in builder   # rule-2: testers only
    # The checks the builder's card closes were added by GuudFlow (the plan named none).
    assert R11 in builder and R12 in builder and "Checks this card closes" in builder
    assert R11 in tester and R11 in reviewer and "stack: the change is written in" in reviewer
    assert "write-cards" in lead and "you write every card" in lead
    assert "review-a-card" in reviewer and "python-pygame" in builder
