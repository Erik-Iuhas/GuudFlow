"""Tests for Track 2a: orphan question respawn.

When an AGENT_MESSAGE is directed at a completed agent, the agent should
be re-queued for one answering iteration (up to 3 times per role per run).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import MockLLMClient, MockTurn
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.workspace import AgentState


def _project(agents: list[AgentConfig], **overrides) -> ProjectConfig:
    base = dict(
        project_id="p_respawn",
        goal="test question respawn",
        agents=agents,
        rbac={},
        max_iterations=3,
    )
    base.update(overrides)
    return ProjectConfig(**base)


def _runner(tmp_path: Path, agents: list[AgentConfig]) -> AgentRunner:
    return AgentRunner(
        project=_project(agents),
        run_id="r-respawn",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )


def _agent_message(from_role: str, to_role: str) -> Signal:
    detail = json.dumps({"to": to_role, "from": from_role, "question": "what is X?"})
    return Signal(
        type=SignalType.AGENT_MESSAGE,
        summary=f"{from_role} → {to_role}: what is X?",
        from_agent=from_role,
        detail=detail,
    )


async def test_question_respawn_requeues_completed_agent(tmp_path: Path):
    """AGENT_MESSAGE to a completed agent removes it from completed_agents and sets READY."""
    agents = [
        AgentConfig(role="developer"),
        AgentConfig(role="requester", depends_on=["developer"]),
    ]
    runner = _runner(tmp_path, agents)

    # Simulate developer completing.
    runner.workspace.completed_agents.append("developer")
    runner.workspace.agent_states["developer"] = AgentState.COMPLETE

    sig = _agent_message("requester", "developer")
    runner.workspace.post_signal(sig)
    # Subscriber fires synchronously on post_signal.

    assert "developer" not in runner.workspace.completed_agents
    assert runner.workspace.agent_states["developer"] is AgentState.READY
    assert "developer" in runner._question_mode_roles
    assert runner._question_respawn_counts["developer"] == 1


async def test_question_respawn_caps_at_three(tmp_path: Path):
    """After 3 respawns the agent stays completed."""
    agents = [
        AgentConfig(role="developer"),
        AgentConfig(role="requester"),
    ]
    runner = _runner(tmp_path, agents)

    for i in range(3):
        runner.workspace.completed_agents.append("developer")
        runner.workspace.agent_states["developer"] = AgentState.COMPLETE
        runner.workspace.post_signal(_agent_message("requester", "developer"))
        assert runner._question_respawn_counts["developer"] == i + 1
        # Simulate the agent completing again for next iteration.
        runner._question_mode_roles.discard("developer")

    # 4th attempt — cap hit; agent stays completed.
    runner.workspace.completed_agents.append("developer")
    runner.workspace.agent_states["developer"] = AgentState.COMPLETE
    runner.workspace.post_signal(_agent_message("requester", "developer"))

    assert "developer" in runner.workspace.completed_agents
    assert runner.workspace.agent_states["developer"] is AgentState.COMPLETE
    assert runner._question_respawn_counts["developer"] == 3


async def test_question_respawn_ignores_active_agent(tmp_path: Path):
    """AGENT_MESSAGE to a non-completed agent has no effect."""
    agents = [AgentConfig(role="developer"), AgentConfig(role="requester")]
    runner = _runner(tmp_path, agents)

    runner.workspace.agent_states["developer"] = AgentState.RUNNING

    runner.workspace.post_signal(_agent_message("requester", "developer"))

    assert "developer" not in runner.workspace.completed_agents
    assert "developer" not in runner._question_mode_roles
    assert runner._question_respawn_counts == {}


async def test_question_respawn_ignores_non_agent_message(tmp_path: Path):
    """Non-AGENT_MESSAGE signals are ignored by the subscriber."""
    agents = [AgentConfig(role="developer")]
    runner = _runner(tmp_path, agents)
    runner.workspace.completed_agents.append("developer")

    runner.workspace.post_signal(
        Signal(type=SignalType.COMPLETE, summary="done", from_agent="developer")
    )

    assert "developer" in runner.workspace.completed_agents
    assert "developer" not in runner._question_mode_roles


async def test_question_respawn_marks_question_mode(tmp_path: Path):
    """Re-queued agent is added to _question_mode_roles so it gets the answer directive."""
    agents = [AgentConfig(role="developer"), AgentConfig(role="requester")]
    runner = _runner(tmp_path, agents)
    runner.workspace.completed_agents.append("developer")
    runner.workspace.agent_states["developer"] = AgentState.COMPLETE

    runner.workspace.post_signal(_agent_message("requester", "developer"))

    assert "developer" in runner._question_mode_roles


async def test_question_mode_cleared_after_completion(tmp_path: Path):
    """_question_mode_roles is cleared when the answering run finishes."""
    agents = [AgentConfig(role="developer"), AgentConfig(role="requester")]
    runner = _runner(tmp_path, agents)
    runner.workspace.completed_agents.append("developer")
    runner.workspace.agent_states["developer"] = AgentState.COMPLETE
    runner.workspace.post_signal(_agent_message("requester", "developer"))

    assert "developer" in runner._question_mode_roles

    # Simulate the runner clearing question_mode after the agent's turn ends.
    runner._question_mode_roles.discard("developer")
    assert "developer" not in runner._question_mode_roles
