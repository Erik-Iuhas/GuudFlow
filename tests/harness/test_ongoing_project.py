"""Plan §13: a project is ongoing work. Real opencode + fake LLM, two runs.

Run 1 takes backlog item B1, the planner must cover it, the builder does the
card, and the run waits for a human decision. Accepting it merges the code
into the project's main and closes B1. Run 2 starts from that main, with the
last accepted run's summary in its briefs, and leftover work returns to the
backlog.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from core import project_runs
from core.backlog import Backlog
from core.project_repo import ProjectRepo
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import SignalType
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from tests.harness.test_run_integration import _settings

pytestmark = requires_opencode

ITEM = ["greet.py defines greet(name) returning 'hi <name>'"]


def _submit(args: dict[str, Any]) -> ToolCall:
    return ToolCall("guudflow_submit_result", args)


def _runner(data: Path, run_id: str, fake: FakeLLM) -> AgentRunner:
    agents = [AgentConfig(role="planner"), AgentConfig(role="builder", depends_on=["planner"])]
    project = ProjectConfig(project_id="p", goal="A small greeting library.",
                            agents=agents, harness="opencode")
    run_root = data / "runs" / run_id
    runner = AgentRunner(project=project, run_id=run_id, run_root=run_root,
                         max_wall_seconds=600)
    # The overseer has its own scenario tests; here a failed card just stays open.
    runner.harness_settings = _settings(fake, overseer="off")
    return runner


def _prepare(data: Path, run_id: str, payload: dict[str, Any]) -> None:
    run_root = data / "runs" / run_id
    project_runs.prepare_run(data, "p", run_id, run_root, run_root / "workspace" / "working",
                             payload, in_place=False)


async def test_two_runs_build_on_the_project(tmp_path: Path, fake_llm: FakeLLM) -> None:
    data = tmp_path / "data"
    backlog = Backlog(data, "p")
    b1 = backlog.add("Greeting function", ITEM, kind="code", size="S", priority=2)
    backlog.add("Docs for greet", ["README shows an example"], kind="docs", size="S")

    # ── run 1: takes B1 ────────────────────────────────────────────────────
    _prepare(data, "run_one", {"backlog_items": [b1.id]})
    plan = {"tasks": [
        {"id": "T1", "title": "Write greet()", "owner_role": "builder", "acceptance": ITEM,
         "size": "S", "kind": "code", "backlog_id": b1.id},
        {"id": "T2", "title": "Add a CLI wrapper", "owner_role": "builder",
         "acceptance": ["cli.py prints greet(argv[1])"], "size": "S", "kind": "code",
         "depends_on": ["T1"]}]}
    fake_llm.route("Your turn: planner",
                   _submit({"tasks": [{**plan["tasks"][1]}]}),   # misses B1: rejected
                   _submit(plan), Text("Planned."))
    fake_llm.route("Your turn: builder",
                   ToolCall("write", {"filePath": "greet.py",
                                      "content": "def greet(name):\n    return f'hi {name}'\n"}),
                   _submit({"status": "done", "task_id": "T1", "summary": "Added greet() in greet.py.",
                            "acceptance": [{"item": ITEM[0], "met": True,
                                            "evidence": "python3 -c ran it"}]}),
                   Text("Done."),
                   _submit({"status": "failed", "task_id": "T2",
                            "summary": "Ran out of time before the CLI."}),
                   Text("Stopping."))
    first = _runner(data, "run_one", fake_llm)
    await first.run()

    planner_reqs = [r for r in fake_llm.requests if "Your turn: planner" in str(r["messages"])]
    assert any("This run's backlog items" in str(r["messages"]) for r in planner_reqs)
    assert any("B1 has no task" in str(r["messages"]) for r in planner_reqs)
    assert first.workspace.tasks["T1"].backlog_id == b1.id

    repo = ProjectRepo(data, "p")
    pending = repo.get("run_one")
    assert pending is not None and pending.state == "pending" and "greet.py" in pending.files
    submitted = [s for s in first.workspace.board if s.type is SignalType.RUN_SUBMITTED]
    assert submitted and '"B1": "done"' in (submitted[-1].detail or "")
    assert backlog.get(b1.id).status == "in_run" and backlog.get(b1.id).pending == "done"
    leftover = [i for i in backlog.items() if i.source == "run:run_one"]
    assert [i.title for i in leftover] == ["Add a CLI wrapper"]   # back on the backlog

    # ── the human accepts it ───────────────────────────────────────────────
    decision = project_runs.decide_run(data, "p", "run_one", accept=True, note="ship it")
    assert decision.run.state == "accepted"
    assert backlog.get(b1.id).status == "done"
    assert "run_one" in project_runs.last_accepted_text(data, "p")

    # ── run 2 starts from the accepted main ────────────────────────────────
    _prepare(data, "run_two", {})
    working = data / "runs" / "run_two" / "workspace" / "working"
    assert (working / "greet.py").read_text().startswith("def greet")
    log = subprocess.run(["git", "log", "--format=%s"], cwd=working, capture_output=True,
                         text=True, check=True).stdout
    assert "merge: run run_one" in log                          # history carried
    fake_llm.route("Your turn: builder",
                   _submit({"status": "done", "summary": "Checked greet; nothing to do."}),
                   Text("Done."))
    second_agents = [AgentConfig(role="builder")]
    second = _runner(data, "run_two", fake_llm)
    second.project.agents = second_agents
    await second.run()
    briefs = [r for r in fake_llm.requests if "Your turn: builder" in str(r["messages"])]
    assert any("Previous run: `run_one`" in str(r["messages"]) for r in briefs)
    assert repo.get("run_two").state == "no_changes"  # type: ignore[union-attr]
