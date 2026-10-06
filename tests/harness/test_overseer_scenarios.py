"""O18/O19: forced stalls end to end on real opencode + a scripted model.

A: review rounds run out on a card → the detectors wake the overseer with a
   situation report → it extends the review and rebriefs the builder → the
   builder fixes the card with the guidance in its brief → the run completes.
B: the overseer itself returns nothing usable → HUMAN_NEEDED, no further work.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from core.harness.detectors import DetectorConfig
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import Signal, SignalType
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from tests.harness.test_run_integration import _settings

pytestmark = requires_opencode

ITEM = "status() returns the string ok"
PLAN = {"notes": "one card", "tasks": [
    {"id": "T1", "title": "Status function", "owner_role": "backend", "acceptance": [ITEM],
     "files_hint": ["api.py"], "size": "S", "kind": "code"}]}


def _submit(args: dict[str, Any]) -> ToolCall:
    return ToolCall("guudflow_submit_result", args)


def _build(body: str, summary: str) -> list[Any]:
    return [ToolCall("write", {"filePath": "api.py", "content": body}),
            _submit({"status": "done", "task_id": "T1", "summary": summary,
                     "acceptance": [{"item": ITEM, "met": True, "evidence": "ran python3 -c"}]}),
            Text("Done.")]


def _review(passed: bool) -> list[Any]:
    return [_submit({"verdict": "pass" if passed else "fail", "task_id": "T1",
                     "items": [{"item": ITEM, "met": passed,
                                "detail": "" if passed else "status() returns None, not 'ok'"}],
                     "summary": "verified" if passed else ""}),
            Text("Reviewed.")]


def _runner(tmp_path: Path, fake: FakeLLM) -> AgentRunner:
    agents = [AgentConfig(role="planner"),
              AgentConfig(role="backend", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["backend"],
                          review_of=["backend"], max_review_rounds=1)]
    project = ProjectConfig(project_id="p", goal="A status function.", agents=agents,
                            harness="opencode")
    runner = AgentRunner(project=project, run_id="stall1", run_root=tmp_path / "run",
                         max_wall_seconds=600)
    runner.harness_settings = _settings(fake, watchdog_interval=0.5,
                                        detectors=DetectorConfig(no_progress_minutes=60))
    return runner


def _briefs(fake: FakeLLM, marker: str) -> list[str]:
    return [str(r["messages"]) for r in fake.tool_requests() if marker in str(r["messages"])]


async def test_overseer_unsticks_an_exhausted_review(tmp_path: Path, fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: planner", _submit(PLAN), Text("Planned."))
    fake_llm.route("Your turn: backend",
                   *_build("def status():\n    pass\n", "Added status() to api.py for the page."),
                   *_build("def status():\n    return None\n", "Reworked status() after review."),
                   *_build("def status():\n    return 'ok'\n", "status() now returns ok."))
    fake_llm.route("Your turn: reviewer", *_review(False), *_review(False), *_review(True))
    fake_llm.route("Your turn: overseer",
                   _submit({"assessment": "The builder keeps returning a non-ok value; the "
                                          "review is right and one more round will fix it.",
                            "interventions": [
                                {"type": "extend_review", "author_role": "backend",
                                 "reviewer_role": "reviewer",
                                 "rationale": "one focused round should be enough"},
                                {"type": "rebrief", "role": "backend",
                                 "addendum": "status() must return the literal string 'ok'.",
                                 "rationale": "the same item failed twice"}]}),
                   Text("Decided."))
    runner = _runner(tmp_path, fake_llm)
    result = await runner.run()
    board = runner.workspace.board
    assert result.status is SchedulerStatus.COMPLETE, [
        f"{s.type.value} {s.summary}" for s in board][-12:]
    assert runner.workspace.tasks["T1"].column == "done"

    # The detectors woke the overseer exactly once, with a situation report.
    overseer_briefs = _briefs(fake_llm, "Your turn: overseer")
    assert overseer_briefs and "Situation report" in overseer_briefs[0]
    for detector in ("needs_attention", "escalation", "same_review_issue"):
        assert detector in overseer_briefs[0], detector
    starts = [s for s in board if s.type is SignalType.STARTED and s.from_agent == "overseer"]
    assert len(starts) == 1

    # Both interventions were posted with their rationale and applied.
    ivs = [json.loads(s.detail or "{}") for s in board
           if s.type is SignalType.OVERSEER_INTERVENTION]
    assert [iv["type"] for iv in ivs] == ["extend_review", "rebrief"]
    third = [b for b in _briefs(fake_llm, "Your turn: backend") if "Guidance from the overseer" in b]
    assert third and "must return the literal string" in third[0]
    assert (runner.git.working_dir / "api.py").read_text().endswith("return 'ok'\n")


async def test_silent_overseer_hands_over_to_a_human(tmp_path: Path, fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: planner", _submit(PLAN), Text("Planned."))
    fake_llm.route("Your turn: backend",
                   *_build("def status():\n    pass\n", "Added status() to api.py for the page."),
                   *_build("def status():\n    return None\n", "Reworked status() after review."))
    fake_llm.route("Your turn: reviewer", *_review(False), *_review(False))
    fake_llm.route("Your turn: overseer", Text("I am not sure."), Text("Still not sure."))
    runner = _runner(tmp_path, fake_llm)

    def stop_on_human(sig: Signal) -> None:
        if sig.type is SignalType.HUMAN_NEEDED and sig.from_agent == "overseer":
            asyncio.get_running_loop().create_task(runner.force_stop())

    runner.workspace.subscribe(stop_on_human)
    result = await asyncio.wait_for(runner.run(), timeout=300)
    assert result.status is SchedulerStatus.STOPPED
    human = [s for s in runner.workspace.board if s.type is SignalType.HUMAN_NEEDED]
    assert human and "overseer failed" in human[0].summary
    assert runner.workspace.tasks["T1"].column == "needs_attention"
