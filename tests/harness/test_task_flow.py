"""O23: a run driven by the task board, end to end on real opencode + fake LLM.

planner → cards → backend ‖ frontend → reviewer, with one review fail that sends
T1 back to its author with the issue in the brief. Columns move only on
scheduler events; the reviewer sees exactly the card's diff.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import SignalType
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from tests.harness.test_run_integration import _settings

pytestmark = requires_opencode

T1_ITEMS = ["api.py defines status() returning the string ok"]
T2_ITEMS = ["ui.py renders the status from api.status()"]


def _submit(args: dict[str, Any]) -> ToolCall:
    return ToolCall("guudflow_submit_result", args)


def _done(tid: str, items: list[str], summary: str) -> ToolCall:
    return _submit({"status": "done", "task_id": tid, "summary": summary,
                    "acceptance": [{"item": i, "met": True, "evidence": "ran it with python3"}
                                   for i in items]})


def _review(tid: str, items: list[str], *, passed: bool) -> ToolCall:
    verdicts = [{"item": i, "met": passed,
                 "detail": "" if passed else "status() returns None, it must return 'ok'"}
                for i in items]
    return _submit({"verdict": "pass" if passed else "fail", "task_id": tid, "items": verdicts,
                    "summary": "verified by running it" if passed else ""})


def _runner(tmp_path: Path, fake: FakeLLM) -> AgentRunner:
    agents = [AgentConfig(role="planner"),
              AgentConfig(role="backend", depends_on=["planner"]),
              AgentConfig(role="frontend", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True,
                          depends_on=["backend", "frontend"], review_of=["backend", "frontend"])]
    project = ProjectConfig(project_id="p", goal="A status API and a page that shows it.",
                            agents=agents, harness="opencode")
    runner = AgentRunner(project=project, run_id="cards1", run_root=tmp_path / "run",
                         max_wall_seconds=600)
    runner.harness_settings = _settings(fake)
    return runner


async def test_cards_drive_the_run_with_a_review_loop(tmp_path: Path, fake_llm: FakeLLM) -> None:
    plan = {"notes": "API first, then the page.", "tasks": [
        {"id": "T1", "title": "Status API module", "owner_role": "backend",
         "acceptance": T1_ITEMS, "files_hint": ["api.py"], "size": "S", "kind": "code"},
        {"id": "T2", "title": "Status page module", "owner_role": "frontend",
         "acceptance": T2_ITEMS, "depends_on": ["T1"], "files_hint": ["ui.py"], "size": "S",
         "kind": "code"}]}
    fake_llm.route("Your turn: planner",
                   _submit({"tasks": [{**plan["tasks"][0], "owner_role": "ghost"}],
                            "notes": "first try"}),            # rejected: unknown owner
                   _submit(plan), Text("Plan submitted."))
    fake_llm.route("Your turn: backend",
                   ToolCall("write", {"filePath": "api.py", "content": "def status():\n    pass\n"}),
                   _done("T1", T1_ITEMS, "Added api.py with a status() function."),
                   Text("Done."),
                   ToolCall("write", {"filePath": "api.py",
                                      "content": "def status():\n    return 'ok'\n"}),
                   _done("T1", T1_ITEMS, "Fixed status() to return ok as the reviewer asked."),
                   Text("Done."))
    fake_llm.route("Your turn: frontend",
                   ToolCall("write", {"filePath": "ui.py",
                                      "content": "import api\nprint(api.status())\n"}),
                   _done("T2", T2_ITEMS, "Added ui.py printing the API status."), Text("Done."))
    fake_llm.route("Your turn: reviewer",
                   ToolCall("guudflow_changes", {"task_id": "T1"}),
                   _review("T1", T1_ITEMS, passed=False), Text("Reviewed."),
                   _review("T1", T1_ITEMS, passed=True), Text("Reviewed."),
                   _review("T2", T2_ITEMS, passed=True), Text("Reviewed."))

    runner = _runner(tmp_path, fake_llm)
    result = await runner.run()
    board = runner.workspace.board
    assert result.status is SchedulerStatus.COMPLETE, [
        f"{s.type.value} {s.summary}" for s in board][-15:]
    assert sorted(result.completed) == ["backend", "frontend", "overseer", "planner", "reviewer"]
    # A clean run never wakes the overseer (it is event-driven, plan §11.1).
    assert not any(s.from_agent == "overseer" and s.type is SignalType.STARTED for s in board)

    # Cards moved only through the scheduler path, T1 twice through review.
    moves = [(d["task_id"], d["to"]) for s in board if s.type is SignalType.TASK_MOVED
             for d in [json.loads(s.detail or "{}")]]
    assert [m for m in moves if m[0] == "T1"] == [
        ("T1", "in_progress"), ("T1", "review"), ("T1", "in_progress"), ("T1", "review"),
        ("T1", "done")]
    assert [m for m in moves if m[0] == "T2"] == [
        ("T2", "in_progress"), ("T2", "review"), ("T2", "done")]
    tasks = runner.workspace.tasks
    assert tasks["T1"].column == "done" and tasks["T1"].review_round == 1

    # The planner's first plan was bounced in the same turn (unknown owner).
    planner_reqs = [r for r in fake_llm.tool_requests() if "Your turn: planner" in str(r["messages"])]
    assert any("unknown owner_role 'ghost'" in str(r["messages"]) for r in planner_reqs)

    # The rework brief carried the review issue from the card thread.
    rework = [str(r["messages"]) for r in fake_llm.tool_requests()
              if "Your turn: backend" in str(r["messages"]) and "review round 1" in str(r["messages"])]
    assert rework and "returns None" in rework[0]

    # The reviewer's card diff showed exactly T1's change.
    diff_reply = next(str(r["messages"]) for r in fake_llm.tool_requests()
                      if "Your turn: reviewer" in str(r["messages"])
                      and "guudflow_changes" in str(r["messages"]))
    assert "api.py" in diff_reply and "ui.py" not in diff_reply.split("guudflow_changes", 1)[1][:2000]

    # Work landed on main.
    main = runner.git.working_dir
    assert (main / "api.py").read_text().endswith("return 'ok'\n")
    assert (main / "ui.py").exists()
    assert (runner.run_root / "tasks.jsonl").exists()
