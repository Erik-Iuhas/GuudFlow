"""The tech lead always writes the cards (worker context plan C)."""

from __future__ import annotations

import pytest

from core.harness.tasks import PlannedTask, PlanResult
from core.plan_drafts import PlanTurn, validate_turn
from core.spec import Spec
from core.team_changes import team_problems
from tests.harness.test_spec_brief import FakeScheduler, _flow, _plan, _sections
from tests.spec_fixtures import with_reasons


def test_a_card_blocked_on_the_planner_rearms_it_for_an_answer_turn(monkeypatch) -> None:
    rearmed: list[str] = []
    monkeypatch.setattr(FakeScheduler, "rearm", lambda self, role, payload=None: rearmed.append(role))
    flow, _ = _flow(None, [])
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="Draw the ship", owner_role="api", size="S", kind="code",
        acceptance=["the ship is drawn"])]))
    turn = flow.begin_turn("api")
    assert turn is not None and turn.card_id == "T1"
    flow._blocked("api", "T1", "planner", "Which sprite size should the ship use?")
    assert rearmed == ["planner"]

    answer = flow.begin_turn("planner")
    assert answer is not None and not answer.skip
    text = _sections(answer)["Cards waiting on you"]
    assert "T1 Draw the ship" in text and "Which sprite size" in text
    assert "not by re-planning" in text
    assert answer.check is not None
    assert answer.check(PlanResult()) != []                     # say something
    reply = PlanResult(notes="Use 32x32 sprites from assets/ship.png.", tasks=[PlannedTask(
        id="T1", title="Add the ship sprite", owner_role="api", size="S", kind="code",
        acceptance=["assets/ship.png is 32x32"], supports=["T1"])])
    assert answer.check(reply) == []
    added = flow.answer_accepted(reply)
    assert added == ["T3"]                                       # T2 was the unblock card
    blocked = flow.board.card("T1")
    assert blocked.column == "todo" and "T3" in blocked.depends_on
    assert flow.board.card("T2").column == "done"
    assert flow.waiting_on_planner() == []
    assert flow.begin_turn("planner").skip                       # nothing waits: skip


def test_the_planning_role_keeps_a_planning_type() -> None:
    team = [{"role": "lead", "role_type": "reviewer", "contract": "tech_lead"},
            {"role": "dev", "role_type": "builder", "depends_on": ["lead"]}]
    assert any("plans the sessions but its role_type is reviewer" in p for p in team_problems(team))


def test_removing_the_tech_lead_while_builders_remain_is_refused() -> None:
    team = [{"role": "lead", "role_type": "architect", "contract": "tech_lead"},
            {"role": "dev", "role_type": "builder", "depends_on": ["lead"]}]
    turn = PlanTurn.model_validate(with_reasons({"reply": "ok", "changes": [
        {"op": "remove_role", "role": "lead", "reason": "trim the team down"}]}))
    problems = validate_turn(turn, Spec({}), {"items": []}, team=team)
    assert any("writes the session's cards" in p for p in problems)


@pytest.mark.parametrize("files,ok", [(["docs/plan.md", "NOTES.md"], True),
                                      (["game/main.py"], False)])
def test_planner_branch_may_only_change_docs(files: list[str], ok: bool) -> None:
    from types import SimpleNamespace

    from core.harness.run import OpencodeRun
    fake = SimpleNamespace(flow=SimpleNamespace(planner="lead"))
    found = OpencodeRun._planner_merge_check(fake, "lead", files)  # type: ignore[arg-type]
    assert (found is None) is ok
    assert OpencodeRun._planner_merge_check(fake, "dev", ["game/main.py"]) is None  # type: ignore[arg-type]


def test_a_pinged_worker_without_a_card_gets_one_turn_with_the_message() -> None:
    flow, _ = _flow(None, [])
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="Draw the ship", owner_role="api", size="S", kind="code",
        acceptance=["the ship is drawn"])]))
    assert not flow.has_work("tester")
    flow.pinged["tester"] = "Add a test for the ship size."
    assert flow.has_work("tester")
    turn = flow.begin_turn("tester")
    assert turn is not None and not turn.skip and turn.card_id is None
    assert "Add a test for the ship size." in (turn.task_text or "")
    assert flow.begin_turn("tester").skip                         # once only
    flow.pinged["api"] = "Ship is 32x32."
    assert flow.begin_turn("api").card_id == "T1" and "api" not in flow.pinged


def test_assigned_items_go_to_their_worker_and_owner_items_get_no_card() -> None:
    from core.backlog import BacklogItem
    flow, _ = _flow(None, [])
    flow.backlog = [BacklogItem(id="B1", title="Draw the ship", assignee="api"),
                    BacklogItem(id="B2", title="Pick the colours", assignee="owner")]
    turn = flow.begin_turn("planner")
    assert turn is not None and turn.check is not None
    text = "\n".join(body for _, body in turn.extra_sections)
    assert "for **api**" in text and "the owner does this one" in text

    def card(owner: str, item: str) -> PlannedTask:
        return PlannedTask(id="T1", title="Draw the ship", owner_role=owner, size="S",
                           kind="code", acceptance=["the ship is drawn"], backlog_id=item)
    assert turn.check(PlanResult(tasks=[card("api", "B1")])) == []        # B2 needs no card
    assert any("assigned to api" in p for p in turn.check(PlanResult(tasks=[card("tester", "B1")])))
    assert any("owner's own item" in p
               for p in turn.check(PlanResult(tasks=[card("api", "B1"),
                                                     card("api", "B2").model_copy(update={"id": "T2"})])))
