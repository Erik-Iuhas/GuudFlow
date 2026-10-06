"""S20: the default one-shot team, end to end on real opencode + fake LLM.

web=off, so the team is planner -> builder_a || builder_b -> reviewer. No network,
no real model: every role is scripted. Every scripted turn ends with Text(...) so
the per-role queues stay aligned across turns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli.oneshot import main as cli_main
from core.oneshot import OneShotRequest, build_oneshot_project, oneshot_summary, render_cost_table
from core.runner import AgentRunner
from core.scheduler import SchedulerStatus
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from tests.harness.test_run_integration import _settings

pytestmark = requires_opencode

T1_ITEMS = ["a.py defines add(a, b) returning the sum"]
T2_ITEMS = ["b.py defines mul(a, b) returning the product"]


def _submit(args: dict[str, Any]) -> ToolCall:
    return ToolCall("guudflow_submit_result", args)


def _done(tid: str, items: list[str], summary: str) -> ToolCall:
    return _submit({"status": "done", "task_id": tid, "summary": summary,
                    "acceptance": [{"item": i, "met": True, "evidence": "ran it with python3"}
                                   for i in items]})


def _pass(tid: str, items: list[str]) -> ToolCall:
    return _submit({"verdict": "pass", "task_id": tid,
                    "items": [{"item": i, "met": True} for i in items],
                    "summary": "verified by running it"})


def _script(fake: FakeLLM) -> None:
    plan = {"notes": "Two independent modules.", "tasks": [
        {"id": "T1", "title": "Addition module", "owner_role": "builder_a",
         "acceptance": T1_ITEMS, "files_hint": ["a.py"], "size": "S", "kind": "code"},
        {"id": "T2", "title": "Multiplication module", "owner_role": "builder_b",
         "acceptance": T2_ITEMS, "files_hint": ["b.py"], "size": "S", "kind": "code"}]}
    fake.route("Your turn: planner", _submit(plan), Text("Plan submitted."))
    fake.route("Your turn: builder_a",
               ToolCall("write", {"filePath": "a.py", "content": "def add(a, b):\n    return a + b\n"}),
               _done("T1", T1_ITEMS, "Added a.py with add()."), Text("Done."))
    fake.route("Your turn: builder_b",
               ToolCall("write", {"filePath": "b.py", "content": "def mul(a, b):\n    return a * b\n"}),
               _done("T2", T2_ITEMS, "Added b.py with mul()."), Text("Done."))
    # The builders run in parallel, so the reviewer may get either card first:
    # pick the verdict from the card named in the brief.
    # The reviewer keeps its session across cards, so look at the newest brief only.
    def verdict(body: dict[str, Any]) -> ToolCall:
        briefs = [m for m in body["messages"] if m.get("role") == "user"
                  and "Your turn: reviewer" in str(m.get("content"))]
        text = str(briefs[-1]["content"] if briefs else body["messages"])
        return _pass("T2", T2_ITEMS) if "Multiplication module" in text else _pass("T1", T1_ITEMS)

    fake.route("Your turn: reviewer", verdict, Text("Reviewed."), verdict, Text("Reviewed."))


async def test_default_team_runs_end_to_end_without_web(tmp_path: Path, fake_llm: FakeLLM) -> None:
    _script(fake_llm)
    project = build_oneshot_project(OneShotRequest(goal="Add and multiply helpers.", web="off",
                                                   budget_usd=5.0, demo=True))
    runner = AgentRunner(project=project, run_id="oneshot1", run_root=tmp_path / "run",
                         max_wall_seconds=600)
    runner.harness_settings = _settings(fake_llm)
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE, [
        f"{s.type.value} {s.summary}" for s in runner.workspace.board][-15:]
    # The overseer is added to task-board runs and counts as complete unless woken.
    assert sorted(result.completed) == ["builder_a", "builder_b", "overseer", "planner", "reviewer"]

    summary = oneshot_summary(runner.run_root, result)
    assert summary["status"] == "complete"
    assert [c["id"] for c in summary["cards"]["done"]] == ["T1", "T2"]
    assert set(summary["cards"]) == {"done"}
    assert {"a.py", "b.py"} <= set(summary["files"])
    costed = {r["role"]: r for r in summary["costs"]}
    for role in ("planner", "builder_a", "builder_b", "reviewer", "TOTAL"):
        assert costed[role]["calls"] >= 1, costed
    assert "TOTAL" in render_cost_table(summary["costs"])
    assert (runner.git.working_dir / "a.py").exists() and (runner.git.working_dir / "b.py").exists()


async def test_cli_runs_the_default_team_and_prints_summary(
        tmp_path: Path, fake_llm: FakeLLM, capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    _script(fake_llm)
    run_dir = tmp_path / "cli_run"
    # cli.main uses asyncio.run, so call it from a worker thread.
    rc = await asyncio.to_thread(
        cli_main, ["Add and multiply helpers.", "--web", "off", "--demo", "--sandbox", "host",
                   "--run-dir", str(run_dir)], settings=_settings(fake_llm))
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "Status: complete" in out
    assert "[started] planner" in out.lower() or "planner" in out
    assert "a.py" in out and "b.py" in out
    assert "Cost per role" in out
