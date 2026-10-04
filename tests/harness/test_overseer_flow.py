"""Tests for core.harness.overseer_flow (plan §11.2–§11.3, O18): each intervention is
applied by the run as card/scheduler operations; caps end in HUMAN_NEEDED."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.git_workspace import GitWorkspace
from core.harness.detectors import RunView
from core.harness.git_roles import RoleClones
from core.harness.overseer import OverseerCaps, OverseerResult
from core.harness.overseer_flow import OverseerFlow
from core.harness.results import AcceptanceCheck, AgentResult, ItemVerdict, ReviewResult
from core.harness.task_board import TaskBoard
from core.harness.tasks import PlannedTask, PlanResult
from core.signals import SignalType
from core.workspace import Workspace


class FakeScheduler:
    def __init__(self) -> None:
        self.running: set[str] = set()
        self.notified = 0

    def is_running(self, role: str) -> bool:
        return role in self.running

    def notify(self) -> None:
        self.notified += 1


def _setup(tmp_path: Path | None = None) -> tuple[OverseerFlow, TaskBoard, Workspace,
                                                  list[tuple[SignalType, str]]]:
    ws = Workspace(run_id="r", goal="g")
    board = TaskBoard(ws, reviewers_of={"a": "rev", "b": "rev"}, review_rounds_for={"rev": 1})
    board.create_from_plan(PlanResult(tasks=[
        PlannedTask(id="T1", title="First card", owner_role="a", acceptance=["it works well"],
                    size="S", kind="code"),
        PlannedTask(id="T2", title="Second card", owner_role="a", acceptance=["it also works"],
                    depends_on=["T1"], size="S", kind="code")]), "planner")
    posted: list[tuple[SignalType, str]] = []
    scheduler = FakeScheduler()
    clones = main_dir = None
    if tmp_path is not None:
        main = GitWorkspace(tmp_path / "working")
        main.init()
        clones, main_dir = RoleClones(main, tmp_path / "roles"), main.working_dir
    flow = OverseerFlow(workspace=ws, overseer_role="overseer",
                        roles={"a": "builder", "b": "builder", "c": "tester", "rev": "reviewer",
                               "overseer": "overseer"},
                        board=board, scheduler=lambda: scheduler,  # type: ignore[arg-type,return-value]
                        post=lambda k, r, s, d: posted.append((k, s)),
                        clones=clones, main_dir=main_dir,
                        caps=OverseerCaps(max_wakes_per_run=2, wake_cooldown_seconds=0))
    return flow, board, ws, posted


def _exhaust(board: TaskBoard, tid: str = "T1") -> None:
    done = AgentResult(status="done", task_id=tid, summary="did the work for the card",
                       acceptance=[AcceptanceCheck(item="it works well", met=True,
                                                   evidence="ran the tests")])
    fail = ReviewResult(verdict="fail", task_id=tid, items=[
        ItemVerdict(item="it works well", met=False, detail="it crashes on empty input")])
    for _ in range(2):
        board.start("a", tid)
        board.submitted(tid, done)
        board.reviewed(tid, "rev", fail)
        board.finish_turn("a")


def _result(*interventions: dict[str, Any]) -> OverseerResult:
    return OverseerResult.model_validate({
        "assessment": "The builder is stuck on the same review issue every round.",
        "interventions": list(interventions)})


def _view(board: TaskBoard, ws: Workspace) -> RunView:
    return RunView(signals=ws.board, cards=board.cards(), role_states={"a": "ready"},
                   running=[])


def test_stall_wakes_then_cap_pauses_for_a_human() -> None:
    flow, board, ws, posted = _setup()
    _exhaust(board)
    assert board.card("T1").column == "needs_attention"
    assert flow.check(_view(board, ws))
    plan = flow.begin_turn(_view(board, ws), {})
    report = dict(plan.extra_sections)["Situation report"]
    assert "needs_attention" in report and "it crashes on empty input" in report
    assert plan.check is not None
    bad = _result({"type": "rebrief", "role": "overseer", "addendum": "x" * 25,
                   "rationale": "rebrief myself please"})
    assert plan.check(bad)                                  # cannot act on itself
    flow.watch.offer([], now=0)
    board.card("T2").column = "needs_attention"
    assert flow.check(_view(board, ws))
    flow.begin_turn(_view(board, ws), {})                   # wake 2 of 2
    board.card("T2").thread.append({"kind": "note", "text": "still stuck"})
    flow.watch._last.clear()                                # noqa: SLF001 — force a new finding
    assert not flow.check(_view(board, ws))                 # cap reached
    assert ws.pending_human is not None
    assert any(k is SignalType.HUMAN_NEEDED for k, _ in posted)


def test_rebrief_reopens_card_and_adds_guidance_once() -> None:
    flow, board, ws, _ = _setup()
    _exhaust(board)
    flow.apply(_result({"type": "rebrief", "role": "a", "task_id": "T1",
                        "addendum": "Handle the empty-input case before anything else.",
                        "rationale": "the same issue failed twice"}))
    assert board.card("T1").column == "todo"
    guidance = flow.brief_addenda("a")
    assert "empty-input" in guidance[0][1] and flow.brief_addenda("a") == ()
    assert any(s.type is SignalType.OVERSEER_INTERVENTION for s in ws.board)


def test_extend_review_grants_one_more_round_as_rework() -> None:
    flow, board, _, _ = _setup()
    _exhaust(board)
    flow.apply(_result({"type": "extend_review", "author_role": "a", "reviewer_role": "rev",
                        "rationale": "one more round should do it"}))
    assert board.review_rounds_for["rev"] == 2
    assert board.card("T1").column == "in_progress"
    assert board.next_for("a").task_id == "T1"


def test_reassign_moves_card_to_same_type_role() -> None:
    flow, board, _, _ = _setup()
    _exhaust(board)
    flow.apply(_result({"type": "reassign", "task_id": "T1", "from_role": "a", "to_role": "b",
                        "rationale": "b already built the client"}))
    card = board.card("T1")
    assert card.assigned_to == "b" and card.column == "todo"


def test_split_replaces_card_and_rewires_dependents() -> None:
    flow, board, _, _ = _setup()
    flow.apply(_result({"type": "split", "task_id": "T1", "rationale": "too big for one turn",
                        "parts": [{"title": "Part one", "acceptance": ["one works"]},
                                  {"title": "Part two", "acceptance": ["two works"]}]}))
    assert board.card("T1").column == "done"
    new = [c for c in board.cards() if c.title in ("Part one", "Part two")]
    assert len(new) == 2 and new[1].depends_on == [new[0].task_id]
    assert board.card("T2").depends_on == [new[1].task_id]
    assert board.next_for("a").task_id == new[0].task_id


def test_decide_records_on_card_and_in_decisions_md(tmp_path: Path) -> None:
    flow, board, _, _ = _setup(tmp_path)
    board.blocked("T1", "b", "Which date format should the API use?")
    flow.apply(_result({"type": "decide", "task_id": "T1",
                        "question": "Which date format should the API use?",
                        "decision": "ISO 8601 strings everywhere",
                        "rationale": "both roles can parse it"}))
    assert board.card("T1").column == "todo"
    assert any(e.kind == "decision" for e in board.thread("T1"))
    text = (tmp_path / "working" / "docs" / "decisions.md").read_text()
    assert "ISO 8601" in text and "both roles can parse it" in text
    assert flow.clones is not None
    assert "decisions" in flow.clones.main.repo.git.log("-1", "--format=%s")


def test_restart_role_resets_clone_once(tmp_path: Path) -> None:
    flow, board, _, _ = _setup(tmp_path)
    assert flow.clones is not None
    work = flow.clones.ensure("a", "A")
    (work / "junk.py").write_text("broken = (\n")
    _exhaust(board)
    flow.apply(_result({"type": "restart_role", "role": "a", "from_ref": "main",
                        "rationale": "the working copy is in a broken state"}))
    assert not (work / "junk.py").exists()
    assert board.card("T1").column == "todo"
    assert flow.team_view().restarts_used == {"a": 1}
    again = _result({"type": "restart_role", "role": "a", "from_ref": "main",
                     "rationale": "try the reset one more time"})
    plan = flow.begin_turn(_view(board, flow.workspace), {})
    assert plan.check is not None and plan.check(again)     # once per role per run


def test_pause_and_team_change_need_a_human() -> None:
    flow, _, ws, _ = _setup()
    flow.apply(_result({"type": "propose_team_change", "summary": "Add a tester role to the team",
                        "change": {"add_role": "tester"}, "rationale": "nobody writes tests"},
                       {"type": "pause", "question": "Should the API support pagination?",
                        "rationale": "the spec is silent and it changes the design"}))
    assert ws.pending_human is not None and "pagination" in ws.pending_human.question
    assert ws.pending_approvals and ws.pending_approvals[-1].kind == "team_change"


def test_overseer_failure_asks_a_human() -> None:
    flow, _, ws, posted = _setup()
    flow.failed("no result was submitted")
    assert ws.pending_human is not None
    assert posted[-1][0] is SignalType.HUMAN_NEEDED
