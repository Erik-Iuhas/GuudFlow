"""Worker context plan H: a session from an approved plan skips the planner's first turn, and
the plan checkpoint pauses before any card starts (approve, or send back to re-plan)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from core.preflight import RUN_PLAN_JSON
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import Signal, SignalType
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from tests.harness.test_run_integration import _settings

pytestmark = requires_opencode

ITEMS = ["api.py defines status() returning the string ok"]
CARD = {"id": "T1", "title": "Status API module", "owner_role": "backend",
        "acceptance": ITEMS, "files_hint": ["api.py"], "size": "S", "kind": "code"}


def _submit(args: dict[str, Any]) -> ToolCall:
    return ToolCall("guudflow_submit_result", args)


def _done(tid: str) -> ToolCall:
    return _submit({"status": "done", "task_id": tid, "summary": "Added api.py with status().",
                    "acceptance": [{"item": i, "met": True, "evidence": "ran it with python3"}
                                   for i in ITEMS]})


def _review(tid: str) -> ToolCall:
    return _submit({"verdict": "pass", "task_id": tid, "summary": "verified by running it",
                    "items": [{"item": i, "met": True, "detail": ""} for i in ITEMS]})


def _runner(tmp_path: Path, fake: FakeLLM, gates: dict[str, Any] | None = None) -> AgentRunner:
    agents = [AgentConfig(role="planner"),
              AgentConfig(role="backend", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["backend"],
                          review_of=["backend"])]
    project = ProjectConfig(project_id="p", goal="A status API.", agents=agents,
                            harness="opencode", approval_gates=gates or {})
    runner = AgentRunner(project=project, run_id="pre1", run_root=tmp_path / "run",
                         max_wall_seconds=600)
    runner.harness_settings = _settings(fake)
    return runner


def _work(fake: FakeLLM, *ids: str) -> None:
    calls: list[Any] = []
    for tid in ids:
        calls += [ToolCall("write", {"filePath": "api.py",
                                     "content": "def status():\n    return 'ok'\n"}),
                  _done(tid), Text("Done.")]
    fake.route("Your turn: backend", *calls)
    fake.route("Your turn: reviewer", *[c for tid in ids for c in (_review(tid), Text("Ok."))])


async def test_an_approved_plan_starts_the_session_without_a_planner_turn(
        tmp_path: Path, fake_llm: FakeLLM) -> None:
    _work(fake_llm, "T1")
    runner = _runner(tmp_path, fake_llm)
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    (tmp_path / "run" / RUN_PLAN_JSON).write_text(json.dumps(
        {"id": "P1", "plan": {"tasks": [CARD], "notes": ""}}))
    result = await runner.run()
    board = runner.workspace.board
    assert result.status is SchedulerStatus.COMPLETE, [f"{s.type.value} {s.summary}"
                                                       for s in board][-15:]
    assert not any("Your turn: planner" in str(r["messages"]) for r in fake_llm.tool_requests())
    assert sorted(runner.workspace.tasks) == ["T1"]
    assert runner.workspace.tasks["T1"].column == "done"
    assert any(s.type is SignalType.COMPLETE and s.from_agent == "planner"
               and "P1" in s.summary for s in board)


def _on_review(runner: AgentRunner, act: Any) -> None:
    """Call `act(n)` (n = 1, 2, ...) soon after each plan-review pause."""
    seen = [0]
    loop = asyncio.get_running_loop()

    def listen(sig: Signal) -> None:
        if sig.type is SignalType.HUMAN_NEEDED and "plan_review" in (sig.detail or ""):
            seen[0] += 1
            loop.call_later(0.05, act, seen[0])
    runner.workspace.subscribe(listen)


def _approve(runner: AgentRunner) -> None:
    ws = runner.workspace
    ws.post_signal(Signal(type=SignalType.HUMAN_RESPONDED, summary="plan approved",
                          from_user=True, iteration=ws.iteration))
    ws.pending_human = None
    ws.controls["wake"]()


async def test_the_checkpoint_pauses_before_any_card_and_resumes_on_approve(
        tmp_path: Path, fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: planner", _submit({"tasks": [CARD], "notes": ""}),
                   Text("Planned."))
    _work(fake_llm, "T1")
    runner = _runner(tmp_path, fake_llm, {"review_plan": True})
    _on_review(runner, lambda n: _approve(runner))
    result = await runner.run()
    board = runner.workspace.board
    assert result.status is SchedulerStatus.COMPLETE, [f"{s.type.value} {s.summary}"
                                                       for s in board][-15:]
    kinds = [s.type for s in board]
    paused = kinds.index(SignalType.HUMAN_NEEDED)
    resumed = kinds.index(SignalType.HUMAN_RESPONDED)
    first_start = next(i for i, s in enumerate(board) if s.type is SignalType.TASK_MOVED
                       and json.loads(s.detail or "{}").get("to") == "in_progress")
    assert paused < resumed < first_start


async def test_sending_the_plan_back_replans_with_the_note(
        tmp_path: Path, fake_llm: FakeLLM) -> None:
    second = {**CARD, "title": "Status API in one module"}
    fake_llm.route("Your turn: planner", _submit({"tasks": [CARD], "notes": ""}),
                   Text("Planned."), _submit({"tasks": [second], "notes": ""}),
                   Text("Planned again."))
    _work(fake_llm, "T2")
    runner = _runner(tmp_path, fake_llm, {"review_plan": True})

    def act(n: int) -> None:
        if n == 1:
            runner.workspace.controls["plan_send_back"]("Keep it to one module, please.")
        else:
            _approve(runner)
    _on_review(runner, act)
    result = await runner.run()
    board = runner.workspace.board
    assert result.status is SchedulerStatus.COMPLETE, [f"{s.type.value} {s.summary}"
                                                       for s in board][-15:]
    assert sorted(runner.workspace.tasks) == ["T2"]               # T1 removed, T2 renumbered
    assert any(s.type is SignalType.TASK_CANCELLED and s.detail == "T1" for s in board)
    replan = [str(r["messages"]) for r in fake_llm.tool_requests()
              if "Your turn: planner" in str(r["messages"])]
    assert any("The owner sent your plan back" in m and "one module" in m for m in replan)
