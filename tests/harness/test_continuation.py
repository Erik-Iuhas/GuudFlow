"""O20 regression: a continuation picks up where the last run stopped.

Replays the run_632863526b situation (.claude/future_buff/bugs/
run_632863526b_circular_ask_cascade.md): a re-run of a project whose previous
run left finished work on disk and one card open. Before, seeded files had no
author, so agents distrusted them, redid them and spiralled into ASK/ANSWER
loops. Now the carry-forward attributes finished work to "previous run, commit
X", the open card comes back with its thread, the planner does not re-plan, and
nobody can ask anybody mid-turn: each role runs exactly once and the run ends.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import SignalType
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall
from tests.harness.test_run_integration import _settings

pytestmark = requires_opencode

OPEN_ITEM = "the state module exposes reset() that clears the board"
PRIOR = {
    "run_id": "run_632863526b", "status": "incomplete", "head": "a98e9c5",
    "done": [{"task_id": "T1", "title": "Architecture docs for the game", "owner": "architect",
              "commit": "a98e9c5"}],
    "open_cards": [{"task_id": "T2", "title": "Game state module", "owner": "game_logic_dev",
                    "column": "in_progress", "kind": "code", "acceptance": [OPEN_ITEM],
                    "review_round": 1,
                    "thread": [{"kind": "review_issue", "author": "reviewer",
                                "text": "reset() is missing entirely", "data": {"item": OPEN_ITEM}}]}],
    "files": ["docs/design/architecture.md"], "decisions": [],
    "text": ("Previous run: `run_632863526b` (incomplete), main at commit `a98e9c5`.\n\n"
             "Done and verified (do not redo these; build on them):\n"
             "- T1 Architecture docs for the game — architect, previous run, commit `a98e9c5`"),
}


def _submit(args: dict[str, object]) -> ToolCall:
    return ToolCall("guudflow_submit_result", args)


async def test_continuation_carries_cards_and_never_spirals(tmp_path: Path,
                                                            fake_llm: FakeLLM) -> None:
    run_root = tmp_path / "run"
    prior_dir = run_root / "workspace" / "inputs" / "from_run_run_632863526b"
    prior_dir.mkdir(parents=True)
    (prior_dir / "carry_forward.json").write_text(json.dumps(PRIOR))

    fake_llm.route("Your turn: planner", _submit({"tasks": [], "notes": "should never run"}))
    fake_llm.route("Your turn: game_logic_dev",
                   ToolCall("write", {"filePath": "state.py",
                                      "content": "BOARD = []\n\ndef reset():\n    BOARD.clear()\n"}),
                   _submit({"status": "done", "task_id": "T1",
                            "summary": "Added reset() to the state module as the review asked.",
                            "acceptance": [{"item": OPEN_ITEM, "met": True,
                                            "evidence": "python3 -c 'import state; state.reset()'"}]}),
                   Text("Done."))
    fake_llm.route("Your turn: reviewer",
                   _submit({"verdict": "pass", "task_id": "T1", "summary": "reset() works",
                            "items": [{"item": OPEN_ITEM, "met": True}]}), Text("Reviewed."))
    agents = [AgentConfig(role="planner"),
              AgentConfig(role="game_logic_dev", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["game_logic_dev"],
                          review_of=["game_logic_dev"])]
    project = ProjectConfig(project_id="roguepack", goal="Finish the roguelike game state.",
                            agents=agents, harness="opencode")
    runner = AgentRunner(project=project, run_id="run_cont", run_root=run_root,
                         max_wall_seconds=300)
    runner.harness_settings = _settings(fake_llm)
    result = await runner.run()
    board = runner.workspace.board
    assert result.status is SchedulerStatus.COMPLETE, [
        f"{s.type.value} {s.summary}" for s in board][-12:]

    # The planner did not re-plan; the open card came back with its thread.
    assert fake_llm.remaining("Your turn: planner") == 1
    assert any("carried 1 open card(s) forward" in s.summary for s in board)
    card = runner.workspace.tasks["T1"]
    assert card.title == "Game state module" and card.column == "done"
    assert any("reset() is missing entirely" in e.get("text", "") for e in card.thread)

    # The brief attributed the previous run's work and carried the open issue.
    brief = next(str(r["messages"]) for r in fake_llm.tool_requests()
                 if "Your turn: game_logic_dev" in str(r["messages"]))
    assert "previous run, commit" in brief and "reset() is missing entirely" in brief

    # Every worker has a name: the board says who is who, and the brief introduces the team.
    team = next(s for s in board if s.type is SignalType.TEAM)
    members = {m["role"]: m["name"] for m in json.loads(team.detail or "{}")["members"]}
    assert set(members) == {"planner", "game_logic_dev", "reviewer", "overseer"}
    assert f"You are **{members['game_logic_dev']}**" in brief
    assert f"**{members['reviewer']}**, reviewer" in brief

    # No spiral: each role ran once, no notes ping-pong, no blocked cycles.
    starts = [s.from_agent for s in board if s.type is SignalType.STARTED]
    assert sorted(starts) == ["game_logic_dev", "reviewer"]
    assert not any(s.type in (SignalType.AGENT_MESSAGE, SignalType.HUMAN_NEEDED) for s in board)

    # The run left its own retrospective and carry-forward for the next one.
    assert json.loads((run_root / "carry_forward.json").read_text())["open_cards"] == []
