"""Flow hardening (docs/FLOW_HARDENING_PLAN.md): owner checks, stop reports, shell and
parked cards in briefs. Each test names the session-5 failure it prevents."""

from __future__ import annotations

from core.harness.capabilities import PermissionInputs, shell_summary
from core.harness.results import AcceptanceCheck, AgentResult, BlockedOn
from core.harness.run_stop import build_stop_report
from core.harness.task_board import TaskBoard
from core.harness.tasks import (
    PlannedTask,
    PlanResult,
    check_task_result,
    is_owner_check,
    split_owner_checks,
)
from core.workspace import Workspace

PLAYTEST = "R16.3: Manual: a new player clears level 1 within three tries"
RAMP = "R16.1: Bun test: ghost speed is 0.8x on level 1"


def _card(tid: str, title: str, owner: str, acceptance: list[str],
          depends_on: list[str] | None = None) -> PlannedTask:
    return PlannedTask(id=tid, title=title, owner_role=owner, acceptance=acceptance,
                       depends_on=depends_on or [], size="S", kind="code")


def _board(*tasks: PlannedTask) -> TaskBoard:
    ws = Workspace(run_id="r", goal="g")
    board = TaskBoard(ws)
    board.create_from_plan(PlanResult(tasks=list(tasks)), "planner")
    return board


def test_owner_checks_by_method_and_by_wording() -> None:
    assert is_owner_check(PLAYTEST)
    assert not is_owner_check(RAMP)
    assert is_owner_check("Browser check: Ghost Blast shows on the first pick screen")
    assert is_owner_check("Human playtest of level 1")
    # The spec's method wins over the wording.
    assert not is_owner_check("R6.9: Manual: HUD shows the family", {"R6.9": "unit"})
    assert is_owner_check("R6.9: HUD shows the family", {"R6.9": "manual"})
    assert split_owner_checks([RAMP, PLAYTEST]) == ([RAMP], [PLAYTEST])


def test_a_card_is_done_without_its_owner_checks() -> None:
    result = AgentResult(status="done", task_id="T3", summary="Ramp implemented and tested.",
                         acceptance=[AcceptanceCheck(item=RAMP, met=True,
                                                     evidence="levels.test.js passes")],
                         owner_checks={PLAYTEST: "start a run, play level 1 three times"})
    assert check_task_result(result, "T3", [RAMP], [PLAYTEST]) == []


def test_blocking_on_an_owner_check_is_refused_in_the_turn() -> None:
    # T3 was blocked on the planner for a human playtest, and the run went stuck.
    blocked = AgentResult(
        status="blocked", task_id="T3", summary="Ramp done; only the playtest remains.",
        acceptance=[AcceptanceCheck(item=RAMP, met=True, evidence="levels.test.js passes"),
                    AcceptanceCheck(item=PLAYTEST, met=False)],
        blocked_on=BlockedOn(role="planner", need="A human playtest that level 1 is clearable."))
    problems = check_task_result(blocked, "T3", [RAMP], [PLAYTEST])
    assert len(problems) == 1 and "owner checks" in problems[0] and "owner_checks" in problems[0]
    # A missing reference screenshot is not a blocker either (T3 in the continuation).
    shot = blocked.model_copy(update={"acceptance": [], "blocked_on": BlockedOn(
        role="planner", need="A reference screenshot of the pickup for the pixel verdict.")})
    assert check_task_result(shot, "T3", [RAMP], []) != []
    # A real block on a teammate still goes through.
    real = blocked.model_copy(update={"acceptance": [], "blocked_on": BlockedOn(
        role="logic", need="Export levelConfigFor() so the renderer can read the ramp.")})
    assert check_task_result(real, "T3", [RAMP], [PLAYTEST]) == []


def test_the_brief_splits_owner_checks_and_lists_other_cards() -> None:
    board = _board(_card("T3", "Ghost ramp", "logic", [RAMP, PLAYTEST]),
                   _card("T4", "Rush factor", "logic", ["x works"], ["T3"]))
    text = board.brief_for("T3")
    checklist, owner = text.split("The owner checks these at acceptance")
    assert RAMP in checklist and PLAYTEST not in checklist and PLAYTEST in owner
    assert "T4 Rush factor (queued after this one)" in text
    review = board.brief_for("T3", reviewer=True)
    assert "do not fail the card on them" in review and "Your other cards" not in review


def test_a_stopped_run_says_what_each_open_card_waits_on() -> None:
    board = _board(_card("T3", "Pickup render", "render", ["a"]),
                   _card("T4", "Magnet render", "render", ["b"], ["T3"]),
                   _card("T1", "Rush factor", "logic", ["c"]))
    board.start("render", "T3")
    board.blocked("T3", "planner", "A reference screenshot of the pickup, please.")
    board.card("T1").column = "done"
    report = build_stop_report("stuck", board.cards(), {"logic": 1}, 5)
    assert report is not None and report.status == "stuck"
    waits = {c.task_id: c.waits_on for c in report.open_cards}
    assert waits["T3"] == "blocked on planner"
    assert waits["T4"].startswith("waits for T3 (render, blocked)")
    assert "T1" not in waits and "nothing could start" in report.summary
    assert build_stop_report("complete", [board.card("T1")]) is None


def test_the_shell_section_names_what_is_allowed() -> None:
    perm = PermissionInputs(edit_allow=("*",), edit_deny=(), bash_default="deny",
                            bash_allow=("ls*", "grep *", "bun test*"), bash_deny=(),
                            task="deny", subagent_depth=None, attach_images=False)
    text = shell_summary(perm)
    assert text is not None and "`bun test`" in text and "`sed`" in text
    assert "never a reason to block" in text


def test_questions_the_brief_answers_do_not_reach_a_person() -> None:
    from core.harness.mcp_tools import answerable_without_human as known
    assert "one part" in known("Is `bun test` allowed? The sed command was denied by permission.")
    assert "current card" in known("Which card is mine, T5 or T7?")
    assert "owner_checks" in known("Can someone do a playtest in the browser?")
    assert known("Should the boss level be harder than level 4?") is None
