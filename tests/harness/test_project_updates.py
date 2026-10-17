"""Project changes during a session reach every worker, and Studio sees who was told."""

from __future__ import annotations

import json
from pathlib import Path

from core.design import Design
from core.harness.project_updates import brief_text, design_changes, spec_changes
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import Signal, SignalType
from core.spec import Spec
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from tests.harness.test_run_integration import _settings


def _spec(**doc: object) -> Spec:
    return Spec({"version": 1, "requirements": [], "decisions": [], "rules": [], **doc})


def test_spec_changes_name_what_a_worker_must_follow() -> None:
    old = _spec(stack={"language": "Python"})
    new = _spec(version=2, stack={"language": "Python 3.12", "framework": "pygame"},
                decisions=[{"id": "D1", "title": "Window", "choice": "800x600",
                            "reason": "fits small laptop screens", "binding": True}],
                rules=[{"id": "rule-1", "text": "No TypeScript", "source": "human"}],
                requirements=[{"id": "R1", "text": "The board shows", "kind": "feature",
                               "priority": "must", "checks": []}])
    lines = spec_changes(old, new)
    assert "Stack — Language: Python 3.12 (was Python)" in lines
    assert any(line.startswith("Stack — Framework") and "pygame" in line for line in lines)
    assert "Decision D1 (binding) Window: 800x600" in lines
    assert any("rule-1" in line and "No TypeScript" in line for line in lines)
    assert "New requirement R1: The board shows" in lines
    assert spec_changes(new, new) == []
    text = brief_text([("U1", lines)])
    assert "follow them over anything earlier" in text and "800x600" in text


def test_design_changes_list_new_components() -> None:
    new = Design({"version": 2, "components": [
        {"id": "C1", "name": "Board", "paths": ["board.py"], "responsibility": "draws", "status": "active"}]})
    assert design_changes(Design({}), new) == ["New component C1 Board (board.py)"]


def _submit(args: dict) -> ToolCall:
    return ToolCall("guudflow_submit_result", args)


ITEMS = ["api.py defines status()"]


@requires_opencode
async def test_a_mid_session_spec_change_reaches_the_next_brief(tmp_path: Path, fake_llm: FakeLLM) -> None:
    from core.plan_drafts import AddDecision, PlanDrafts
    data_root = tmp_path / "data"
    (data_root / "projects" / "p").mkdir(parents=True)
    PlanDrafts(data_root, "p").db.read_spec_doc()            # creates work.db
    card = {"id": "T1", "title": "Status API module", "owner_role": "backend",
            "acceptance": ITEMS, "size": "S", "kind": "code"}
    fake_llm.route("Your turn: planner", _submit({"tasks": [card], "notes": ""}), Text("Planned."))
    fake_llm.route("Your turn: backend",
                   ToolCall("write", {"filePath": "api.py", "content": "def status():\n    return 1\n"}),
                   _submit({"status": "done", "task_id": "T1", "summary": "Added api.py with a status() function.",
                            "acceptance": [{"item": i, "met": True, "evidence": "ran it with python3"}
                                           for i in ITEMS]}), Text("Done."))
    fake_llm.route("Your turn: reviewer",
                   _submit({"verdict": "pass", "task_id": "T1", "summary": "verified by running it",
                            "items": [{"item": i, "met": True, "detail": ""} for i in ITEMS]}),
                   Text("Ok."))
    agents = [AgentConfig(role="planner"), AgentConfig(role="backend", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["backend"],
                          review_of=["backend"])]
    runner = AgentRunner(project=ProjectConfig(project_id="p", goal="A status API.", agents=agents,
                                               harness="opencode"),
                         run_id="upd1", run_root=data_root / "runs" / "upd1", max_wall_seconds=600)
    runner.harness_settings = _settings(fake_llm)

    def listen(sig: Signal) -> None:
        if sig.type is SignalType.COMPLETE and sig.from_agent == "planner":
            PlanDrafts(data_root, "p").apply_direct(AddDecision(
                op="add_decision", ref="new:1", title="Return type", choice="status() returns a string",
                reason="the page prints it as text for the owner"), "human")
    runner.workspace.subscribe(listen)
    runner.workspace.post_signal(Signal(type=SignalType.USER_COMMENT, summary="Use snake_case names",
                                        from_user=True))
    result = await runner.run()
    board = runner.workspace.board
    assert result.status is SchedulerStatus.COMPLETE, [f"{s.type.value} {s.summary}" for s in board][-12:]
    updated = [s for s in board if s.type is SignalType.PROJECT_UPDATED]
    assert len(updated) == 1 and "returns a string" in (updated[0].detail or "")
    told = {json.loads(s.detail or "{}")["role"] for s in board if s.type is SignalType.DELIVERED
            and json.loads(s.detail or "{}").get("kind") == "project_update"}
    assert told == {"backend", "reviewer"}
    got = {json.loads(s.detail or "{}")["role"] for s in board if s.type is SignalType.DELIVERED
           and json.loads(s.detail or "{}").get("kind") == "comment"}
    assert {"planner", "backend", "reviewer"} <= got
    backend = [str(r["messages"]) for r in fake_llm.tool_requests() if "Your turn: backend" in str(r["messages"])]
    assert backend and "What changed in the project" in backend[0] and "returns a string" in backend[0]
