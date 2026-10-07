"""The spec in briefs: planner, card and reviewer sections, rule lines, start-mode defaults."""

from __future__ import annotations

from pathlib import Path

from core.backlog import BacklogItem
from core.harness.results import AcceptanceCheck, AgentResult, ItemVerdict, ReviewResult
from core.harness.spec_brief import SpecBrief
from core.harness.task_flow import TaskFlow
from core.harness.tasks import PlannedTask, PlanResult
from core.runner import AgentConfig
from core.signals import SignalType
from core.start_modes import DEFAULT_RULES, PLANNER_TEXT
from core.workspace import Workspace
from tests.spec_fixtures import PREF, RULE1, RULE2, seed_spec

R11 = "R1.1: a saved recipe shows in the list"
R12 = "R1.2: an empty title is refused"
OWN = "the list screen renders"


class FakeScheduler:
    max_iterations = 12

    def is_running(self, role: str) -> bool:
        return False

    def rearm(self, role: str, payload: object = None) -> None:
        pass

    def notify(self) -> None:
        pass


def _flow(brief: SpecBrief | None, items: list[BacklogItem], posted: list | None = None
          ) -> tuple[TaskFlow, Workspace]:
    ws = Workspace(run_id="r", goal="g")
    agents = [AgentConfig(role="planner"),
              AgentConfig(role="api", depends_on=["planner"]),
              AgentConfig(role="tester", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["api", "tester"],
                          review_of=["api", "tester"])]
    types = {"planner": "planner", "api": "builder", "tester": "tester", "reviewer": "reviewer"}
    sink = posted if posted is not None else []
    flow = TaskFlow.build(agents, types, ws, lambda: FakeScheduler(),  # type: ignore[arg-type,return-value]
                          post=lambda k, r, s, d: sink.append((k, s, d)),
                          escalate=lambda r, s, d: None)
    assert flow is not None
    flow.backlog, flow.spec = items, brief
    return flow, ws


def _item(**kw: object) -> BacklogItem:
    return BacklogItem(id="B1", title="Save recipes", acceptance=[R11, R12], serves=["R1"],
                       **kw)  # type: ignore[arg-type]


def _plan(owner: str = "api", acceptance: list[str] | None = None) -> PlanResult:
    return PlanResult(tasks=[PlannedTask(
        id="T1", title="Recipe saving", owner_role=owner, backlog_id="B1", size="S",
        kind="code", acceptance=acceptance if acceptance is not None else [R11, R12, OWN])])


def _sections(plan: object) -> dict[str, str]:
    return dict(plan.extra_sections)  # type: ignore[attr-defined]


def _to_review(flow: TaskFlow, owner: str = "api") -> str:
    """Plan accepted, the owner's turn begun and its done result merged: card is in review."""
    return _to_review_turn(flow, owner)[0]


def _to_review_turn(flow: TaskFlow, owner: str = "api") -> tuple[str, object]:
    turn = flow.begin_turn(owner)
    assert turn is not None and turn.card_id
    flow.board.submitted(turn.card_id, AgentResult(
        status="done", task_id=turn.card_id, summary="Implemented the recipe saving flow.",
        acceptance=[AcceptanceCheck(item=i, met=True, evidence="ran the tests, all green")
                    for i in flow.board.card(turn.card_id).acceptance_criteria]))
    return turn.card_id, turn


# ── loading ─────────────────────────────────────────────────────────────────
def test_nothing_without_a_spec_or_a_start_mode(tmp_path: Path) -> None:
    assert SpecBrief.load(tmp_path, "p1", None) is None
    assert SpecBrief.load(tmp_path, "p1", "not-a-mode") is None
    assert SpecBrief.load(None, None, None) is None
    (tmp_path / "projects" / "p1").mkdir(parents=True)        # a project, but no work.db
    assert SpecBrief.load(tmp_path, "p1", None) is None


def test_a_start_mode_alone_or_a_spec_alone_loads(tmp_path: Path) -> None:
    only_mode = SpecBrief.load(tmp_path, "p1", "maintain")
    assert only_mode is not None and only_mode.start_mode == "maintain"
    seed_spec(tmp_path)
    brief = SpecBrief.load(tmp_path, "p1", None)
    assert brief is not None and brief.start_mode is None and brief.spec.version == 3


# ── planner ─────────────────────────────────────────────────────────────────
def test_planner_section(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    brief = SpecBrief.load(tmp_path, "p1", "test_first")
    flow, _ = _flow(brief, [_item()])
    turn = flow.begin_turn("planner")
    assert turn is not None
    sections = _sections(turn)
    text = sections["The spec"]
    assert "**R1** [feature, must]" in text and "R1.1: a saved recipe shows in the list" in text
    assert "R2" not in text.split("Binding decisions")[0]      # R2 is not served by B1
    assert "D1" in text and "SQLite" in text                    # binding decision
    assert "D2" not in text                                     # not binding
    assert RULE1 in text and RULE2 in text and PREF in text
    assert "(preference)" in text
    assert "Keep every `R#.#:` acceptance line of an item on the card that implements it, " \
           "word for word." in text
    assert PLANNER_TEXT["test_first"] in text
    assert list(sections)[:2] == ["This run's backlog items", "The spec"]


def test_planner_without_spec_or_mode_is_unchanged() -> None:
    flow, _ = _flow(None, [_item()])
    turn = flow.begin_turn("planner")
    assert turn is not None
    assert list(_sections(turn)) == ["This run's backlog items", "Budget and card size",
                                     "Your output: the task list"]


def test_soft_coverage_warning(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    posted: list = []
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()], posted)
    flow.plan_accepted(_plan(acceptance=[R11, OWN]))           # R1.2 is on no card
    assert flow.board.planned                                   # the plan stands
    notes = [p for p in posted if "spec coverage" in p[1]]
    assert len(notes) == 1 and notes[0][0] is SignalType.THINKING and "R1.2" in notes[0][1]
    assert "R1.1" not in notes[0][1]
    posted.clear()
    flow2, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()], posted)
    flow2.plan_accepted(_plan())                                # all lines kept: quiet
    assert not [p for p in posted if "spec coverage" in p[1]]


# ── card roles ──────────────────────────────────────────────────────────────
def test_builder_card_section(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    flow.plan_accepted(_plan("api"))
    turn = flow.begin_turn("api")
    assert turn is not None
    text = _sections(turn)["The spec"]
    assert "Requirements this card serves" in text and "**R1**" in text and R11 in text
    assert "SQLite" in text and RULE1 in text
    assert RULE2 not in text                                    # rule-2 is for testers only
    assert f"(preference) {PREF}" in text
    assert "Review checklist" not in text


def test_builder_check_ignores_rule_lines(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    flow.plan_accepted(_plan("api"))
    turn = flow.begin_turn("api")
    assert turn is not None and turn.check is not None
    result = AgentResult(status="done", task_id="T1", summary="Implemented the saving flow.",
                         acceptance=[AcceptanceCheck(item=i, met=True,
                                                     evidence="ran the tests, all green")
                                     for i in (R11, R12, OWN)])
    assert turn.check(result) == []                             # no rule line asked of builders


def test_card_without_an_item_gets_decisions_and_rules_only(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="An extra cleanup", owner_role="api", size="S", kind="code",
        acceptance=["the cleanup is done"])]))
    turn = flow.begin_turn("api")
    assert turn is not None
    text = _sections(turn)["The spec"]
    assert "Requirements this card serves" not in text and RULE1 in text


# ── reviewer ────────────────────────────────────────────────────────────────
def test_reviewer_gets_rule_lines_and_must_answer_them(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    flow.plan_accepted(_plan("api"))
    _to_review(flow)
    turn = flow.begin_turn("reviewer")
    assert turn is not None and turn.check is not None
    text = _sections(turn)["The spec"]
    rule1 = f"rule-1: {RULE1}"
    assert f"- [ ] {rule1}" in text
    checklist = text.split("Review checklist additions")[1]
    assert "rule-2" not in checklist and "rule-3" not in checklist   # testers only / preference
    assert "(preference) Prefer small functions" in text
    card_items = [ItemVerdict(item=i, met=True) for i in (R11, R12, OWN)]

    missing = ReviewResult(verdict="pass", task_id="T1", items=card_items)
    problems = turn.check(missing)
    assert problems == [f"items: missing a verdict for '{rule1}'"]

    full = ReviewResult(verdict="pass", task_id="T1",
                        items=card_items + [ItemVerdict(item=rule1, met=True)])
    assert turn.check(full) == []
    leading_id = ReviewResult(verdict="pass", task_id="T1",
                              items=card_items + [ItemVerdict(item=f"rule-1: {RULE1[:12]}",
                                                              met=True)])
    assert turn.check(leading_id) == []                        # prefix match, like card lines


def test_rules_follow_the_owners_role_type(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    flow.plan_accepted(_plan("tester"))
    _to_review(flow, "tester")
    turn = flow.begin_turn("reviewer")
    assert turn is not None
    text = _sections(turn)["The spec"]
    assert f"rule-2: {RULE2}" in text and f"rule-1: {RULE1}" in text


def test_reviewer_also_answers_the_mode_lines(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", "test_first"), [_item()])
    flow.plan_accepted(_plan("api"))
    _to_review(flow)
    turn = flow.begin_turn("reviewer")
    assert turn is not None and turn.check is not None
    assert "mode: the tests were written before the code and now pass" in _sections(turn)["The spec"]
    items = [ItemVerdict(item=i, met=True) for i in
             (R11, R12, OWN, f"rule-1: {RULE1}")]
    problems = turn.check(ReviewResult(verdict="pass", task_id="T1", items=items))
    assert problems == ["items: missing a verdict for "
                        "'mode: the tests were written before the code and now pass'"]


def test_an_unmet_rule_fails_the_review_through_the_board(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    flow.plan_accepted(_plan("api"))
    card_id = _to_review(flow)
    turn = flow.begin_turn("reviewer")
    assert turn is not None
    result = ReviewResult(verdict="fail", task_id=card_id, items=[
        *[ItemVerdict(item=i, met=True) for i in (R11, R12, OWN)],
        ItemVerdict(item=f"rule-1: {RULE1}", met=False,
                    detail="price is stored as a float in recipes.py line 12")])
    assert turn.check is not None and turn.check(result) == []
    flow.apply_result("reviewer", card_id, result, "")
    assert flow.board.card(card_id).column == "in_progress"    # back to its author


# ── start-mode defaults ─────────────────────────────────────────────────────
def test_maintain_brings_two_default_rules_without_a_spec(tmp_path: Path) -> None:
    brief = SpecBrief.load(tmp_path, "p1", "maintain")
    assert brief is not None
    texts = [r.text for r in brief.rules("builder")]
    assert texts == ["Make the smallest change that meets the item",
                     "Every fix gets a regression test"]
    assert [d.id for d in DEFAULT_RULES["maintain"]] == ["rule-m1", "rule-m2"]
    flow, _ = _flow(brief, [BacklogItem(id="B1", title="Fix the crash")])
    sections = _sections(flow.begin_turn("planner"))           # type: ignore[arg-type]
    assert "Make the smallest change that meets the item" in sections["The spec"]
    assert "Every fix gets a regression test" in sections["The spec"]
    assert PLANNER_TEXT["maintain"] in sections["The spec"]
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="Fix the crash", owner_role="api", backlog_id="B1", size="S",
        kind="code", acceptance=["no crash on empty input"])]))
    _to_review(flow)
    review = flow.begin_turn("reviewer")
    assert review is not None
    assert "rule-m1: Make the smallest change that meets the item" in _sections(review)["The spec"]


def test_maintain_defaults_are_not_repeated_when_the_spec_says_them(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    from core.spec import Rule
    from core.work_db import WorkDB
    with WorkDB(tmp_path / "projects" / "p1").spec_doc() as doc:
        doc["rules"].append(Rule(id="rule-4", text="every fix gets a regression test",
                                 source="human").to_dict())
    brief = SpecBrief.load(tmp_path, "p1", "maintain")
    assert brief is not None
    texts = [r.text for r in brief.rules("builder")]
    assert texts.count("every fix gets a regression test") == 1
    assert "Every fix gets a regression test" not in texts
    assert "Make the smallest change that meets the item" in texts


def test_other_modes_have_no_default_rules(tmp_path: Path) -> None:
    for mode in ("spec_first", "prototype_first", "test_first", "research_first"):
        brief = SpecBrief.load(tmp_path, "p1", mode)
        assert brief is not None and brief.rules("builder") == []


# ── unchanged behaviour ─────────────────────────────────────────────────────
def test_no_spec_no_mode_changes_nothing_for_cards() -> None:
    flow, _ = _flow(None, [_item()])
    flow.plan_accepted(_plan("api"))
    _, builder = _to_review_turn(flow)
    assert builder.extra_sections == ()                         # type: ignore[attr-defined]
    reviewer = flow.begin_turn("reviewer")
    assert reviewer is not None and reviewer.extra_sections == () and reviewer.check is not None
    items = [ItemVerdict(item=i, met=True) for i in (R11, R12, OWN)]
    assert reviewer.check(ReviewResult(verdict="pass", task_id="T1", items=items)) == []


# ── why a card exists (cards link to the spec) ──────────────────────────────
def test_card_serves_combines_own_lines_and_item() -> None:
    from core.harness.tasks import card_serves
    assert card_serves([R11, OWN], ["R2"]) == ["R2", "R1.1"]
    assert card_serves([OWN], [], ["R1"]) == ["R1"]                # falls back to the item
    assert card_serves([R12], [], ["R1"]) == ["R1.2"]              # own lines win


def test_plan_check_wants_a_reason_per_card(tmp_path: Path) -> None:
    from core.harness.tasks import check_plan
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    turn = flow.begin_turn("planner")
    assert turn is not None
    helper = PlannedTask(id="T2", title="Shared helper", owner_role="api", size="S",
                         kind="code", acceptance=[OWN])
    plan = PlanResult(tasks=[_plan().tasks[0], helper])
    problems = turn.check(plan)
    assert len(problems) == 1 and "T2: say why this card exists" in problems[0]
    helper.supports = ["T1"]
    assert turn.check(plan) == []
    helper.serves, helper.supports = ["R9.9"], []
    assert any("serves 'R9.9'" in p for p in turn.check(plan))
    helper.serves, helper.supports = [], ["T7"]
    assert any("supports 'T7'" in p for p in check_plan(plan, ["api"], ["B1"]))
    # Without a spec nothing is asked.
    helper.supports = []
    assert check_plan(plan, ["api"], ["B1"]) == []


def test_cards_store_what_they_serve(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    plan = _plan(acceptance=[OWN])
    plan.tasks.append(PlannedTask(id="T2", title="Shared helper", owner_role="tester",
                                  size="S", kind="code", acceptance=[R12], supports=["T1"]))
    flow.plan_accepted(plan)
    assert flow.board.card("T1").serves == ["R1"]                  # from its item
    assert flow.board.card("T2").serves == ["R1.2"]
    assert flow.board.card("T2").supports == ["T1"]
    flow.board.blocked("T1", "tester", "the fixture loader is missing")
    extra = [c for c in flow.board.cards() if c.task_id not in ("T1", "T2")][0]
    assert extra.supports == ["T1"]


def test_card_brief_names_the_checks_it_is_for(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    flow, _ = _flow(SpecBrief.load(tmp_path, "p1", None), [_item()])
    flow.plan_accepted(_plan(acceptance=[R11, OWN]))
    turn = flow.begin_turn("api")
    assert turn is not None
    text = _sections(turn)["The spec"]
    assert "This card is for R1.1." in text and "**R1**" in text
