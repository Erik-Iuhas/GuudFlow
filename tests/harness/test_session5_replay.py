"""Replay of pacrogue session 5 (docs/FLOW_HARDENING_PLAN.md §9).

Every place session 5 needed an operator, driven through the real board, flow, detectors,
stop report and backlog, with no LLM: each must now resolve without a person.
"""

from __future__ import annotations

from pathlib import Path

from core.backlog import Backlog, CardOutcome
from core.harness.detectors import DetectorConfig, RunView, blocked_chain
from core.harness.results import AcceptanceCheck, AgentResult, BlockedOn, ItemVerdict, ReviewResult
from core.harness.run_stop import build_stop_report
from core.harness.task_flow import TaskFlow, read_owner_checks
from core.harness.tasks import PlannedTask, PlanResult, check_plan
from core.runner import AgentConfig
from core.workspace import Workspace

RAMP = "R16.1: Bun test: ghost speed is 0.8x on level 1 and 1.0x on level 5"
PLAYTEST = "R16.3: Manual: a new player clears level 1 within three tries"


class FakeScheduler:
    def __init__(self) -> None:
        self.rearmed: list[str] = []
        self.max_iterations = 12

    def is_running(self, role: str) -> bool:
        return False

    def rearm(self, role: str, payload: object = None) -> None:
        self.rearmed.append(role)

    def notify(self) -> None:
        pass


def _flow(tmp_path: Path) -> tuple[TaskFlow, Workspace]:
    ws = Workspace(run_id="r", goal="pacrogue")
    agents = [AgentConfig(role="planner"),
              AgentConfig(role="logic", depends_on=["planner"]),
              AgentConfig(role="render", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["logic", "render"],
                          review_of=["logic", "render"])]
    types = {"planner": "planner", "logic": "builder", "render": "frontend_builder",
             "reviewer": "reviewer"}
    flow = TaskFlow.build(agents, types, ws, lambda: FakeScheduler(),  # type: ignore[arg-type,return-value]
                          post=lambda *a: None, escalate=lambda *a: None)
    assert flow is not None
    flow.run_root = tmp_path
    flow.plan_accepted(PlanResult(tasks=[
        PlannedTask(id="T3", title="Ghost speed ramp", owner_role="logic", backlog_id="B16",
                    acceptance=[RAMP, PLAYTEST], size="S", kind="code"),
        PlannedTask(id="T4", title="Pickup render", owner_role="render", backlog_id="B15",
                    acceptance=["Bun test: the pickup draws in palette colours"],
                    size="S", kind="code"),
        PlannedTask(id="T5", title="Magnet render", owner_role="render", backlog_id="B7",
                    acceptance=["Bun test: pulled pellets slide toward Pac-Man"],
                    depends_on=["T4"], size="S", kind="code")]))
    ws.completed_agents.append("planner")
    return flow, ws


def test_a_manual_check_never_blocks_the_card(tmp_path: Path) -> None:
    """Session 5 T3 / F13: Kenji blocked T3 on the planner for a human playtest."""
    flow, _ = _flow(tmp_path)
    turn = flow.begin_turn("logic")
    assert turn is not None and turn.card_id == "T3" and turn.check is not None
    assert "The owner checks these at acceptance" in (turn.task_text or "")
    tried = AgentResult(
        status="blocked", task_id="T3", summary="Ramp implemented; only the playtest is open.",
        acceptance=[AcceptanceCheck(item=RAMP, met=True, evidence="levels.test.js: 39 pass")],
        blocked_on=BlockedOn(role="planner", need="A human playtest that a new player clears level 1."))
    assert any("owner checks" in p for p in turn.check(tried))     # bounced in the same turn
    done = tried.model_copy(update={
        "status": "done", "blocked_on": None,
        "owner_checks": {PLAYTEST: "start a run and play level 1 three times"}})
    assert turn.check(done) == []
    flow.apply_result("logic", "T3", done, commit="abc")
    flow.end_turn("logic")
    assert flow.board.card("T3").column == "review"
    review = flow.begin_turn("reviewer")
    assert review is not None and review.check is not None
    verdict = ReviewResult(verdict="pass", task_id="T3", summary="Ramp values tested and met.",
                           items=[ItemVerdict(item=RAMP, met=True)])
    assert review.check(verdict) == []                 # no verdict owed on the playtest
    flow.apply_result("reviewer", "T3", verdict, commit="abc")
    assert flow.board.card("T3").column == "done"
    rows = read_owner_checks(tmp_path)
    assert [(r["task_id"], r["item"], r["how"]) for r in rows] == [
        ("T3", PLAYTEST, "start a run and play level 1 three times")]


def test_a_card_blocked_on_the_finished_tech_lead_reaches_the_overseer(tmp_path: Path) -> None:
    """Session 5 continuation: Lena blocked T4 on the planner; T5 waited behind it; stuck."""
    flow, ws = _flow(tmp_path)
    flow.board.start("render", "T4")
    flow.apply_result("render", "T4", AgentResult(
        status="blocked", task_id="T4", summary="Pickup drawn; the palette for it is unclear.",
        blocked_on=BlockedOn(role="planner", need="Which palette colour does the pickup use?")),
        commit="abc")
    flow.end_turn("render")
    view = RunView(signals=[], cards=flow.board.cards(), running=["logic"],
                   role_states={"planner": "complete", "logic": "running", "render": "ready"})
    stalls = blocked_chain(view, DetectorConfig())
    assert stalls and "finished for this run" in stalls[0].detail     # overseer wakes now
    flow.board.reopen("T4", "overseer decision: use the pellet colour")  # its `decide`
    assert flow.board.card("T4").column == "todo"
    assert flow.board.next_for("render").task_id == "T4"
    assert all(c.column == "done" for c in flow.board.cards("planner"))


def test_a_stopped_run_says_why_and_continues_with_only_open_items(tmp_path: Path) -> None:
    """Session 5: two runs ended `stuck` silently; continuing re-planned six done items."""
    flow, _ = _flow(tmp_path)
    flow.board.card("T3").column = "done"
    report = build_stop_report("stuck", flow.board.cards(), {"render": 2}, 6)
    assert report is not None and report.open_items == ["B15", "B7"]
    assert {c.task_id: c.waits_on for c in report.open_cards}["T5"].startswith("waits for T4")
    backlog = Backlog(tmp_path / "data", "p")
    ids = {t: backlog.add(t, ["x"]).id for t in ("ramp", "pickup", "magnet")}
    backlog.take_for_run("run_1", ids=list(ids.values()))
    backlog.record_outcomes("run_1", [CardOutcome("ramp", "done", ids["ramp"]),
                                      CardOutcome("pickup", "todo", ids["pickup"]),
                                      CardOutcome("magnet", "todo", ids["magnet"])])
    carried = backlog.transfer("run_1", "run_2")
    open_ids = [i.id for i in carried if i.pending != "done"]
    assert open_ids == [ids["pickup"], ids["magnet"]]
    plan = PlanResult(tasks=[
        PlannedTask(id="T1", title="Pickup render", owner_role="render",
                    backlog_id=ids["pickup"], acceptance=["draws"], size="S", kind="code"),
        PlannedTask(id="T2", title="Magnet render", owner_role="render",
                    backlog_id=ids["magnet"], acceptance=["slides"], size="S", kind="code")])
    assert check_plan(plan, ["render"], open_ids) == []        # no card owed for the ramp
