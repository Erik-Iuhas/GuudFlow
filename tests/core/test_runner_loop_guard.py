"""End-to-end tests: LoopGuard wired into the runner's tool-use loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import LLMMessage, MockLLMClient, MockTurn, ToolCall
from core.loop_guard import LoopGuardConfig
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


async def test_runner_nudges_on_repeated_identical_failure(tmp_path: Path):
    """The LLM keeps calling fs:read on a path that doesn't exist. After
    3 identical failures, the runner injects a [LOOP GUARD] nudge into the
    next user turn so the LLM sees the explicit hint."""
    project = ProjectConfig(
        project_id="p1", goal="Read a file",
        agents=[AgentConfig(
            role="developer",
            description="reads files",
            declared_local_tools=["fs-read"],
            model_tier="solid",
            max_tool_iterations=10,
            loop_guard=LoopGuardConfig(max_repeat_identical=3, max_repeat_error_class=20),
        )],
        rbac={"developer": RoleRBAC(role="developer", allowed_scopes=[])},
        max_iterations=2,
    )

    # Script: the LLM keeps trying fs:read on ghost.py. After the 3rd
    # consecutive identical failure, the runner appends a nudge; we then
    # script the LLM to give up cleanly.
    bad_call = ToolCall(id="t1", name="fs-read", arguments={"path": "ghost.py"})
    bad_call_2 = ToolCall(id="t2", name="fs-read", arguments={"path": "ghost.py"})
    bad_call_3 = ToolCall(id="t3", name="fs-read", arguments={"path": "ghost.py"})
    llm = MockLLMClient([
        MockTurn(tool_calls=[bad_call]),
        MockTurn(tool_calls=[bad_call_2]),
        MockTurn(tool_calls=[bad_call_3]),
        MockTurn(text="(giving up — file does not exist)"),
    ])

    runner = AgentRunner(
        project=project, run_id="r_loop_test",
        run_root=tmp_path / "run", llm=llm,
        max_wall_seconds=None,
    )
    runner._build_agents()
    cfg = project.agents[0]
    agent = runner.agents["developer"]
    result = await runner._generic_tool_use_loop(agent, cfg)

    # Verify the LLM was given the loop-guard nudge as a user turn.
    # Find a user message containing [LOOP GUARD].
    messages_seen = [
        call for call in llm.calls
        if any(
            isinstance(m.content, str) and "[LOOP GUARD]" in m.content
            for m in call["messages"]
        )
    ]
    assert messages_seen, "expected a [LOOP GUARD] nudge to appear in some LLM call"
    assert "(giving up" in result


async def test_runner_aborts_on_wall_clock_deadline(tmp_path: Path):
    """If the LoopGuard wall-clock deadline is exceeded, the runner aborts
    the agent's run with a clear message. Using a negative max_wall_seconds
    ensures the deadline fires regardless of clock resolution on the host."""
    project = ProjectConfig(
        project_id="p1", goal="x",
        agents=[AgentConfig(
            role="developer",
            description="dev",
            declared_local_tools=["fs-read"],
            max_tool_iterations=10,
            loop_guard=LoopGuardConfig(max_wall_seconds=-1.0),
        )],
        rbac={},
        max_iterations=2,
    )

    bad_call = ToolCall(id="t1", name="fs-read", arguments={"path": "x.py"})
    llm = MockLLMClient([
        MockTurn(tool_calls=[bad_call]),
        MockTurn(text="should not get here"),
    ])

    runner = AgentRunner(
        project=project, run_id="r_dl",
        run_root=tmp_path / "run", llm=llm,
        max_wall_seconds=None,
    )
    runner._build_agents()
    cfg = project.agents[0]
    agent = runner.agents["developer"]
    result = await runner._generic_tool_use_loop(agent, cfg)
    assert "loop guard" in result.lower()
    assert "wall-clock" in result.lower() or "budget" in result.lower()


async def test_runner_sets_workspace_deadline(tmp_path: Path):
    """When max_wall_seconds is provided, the workspace's deadline_at is
    populated for the scheduler to enforce."""
    project = ProjectConfig(project_id="p1", goal="x", agents=[], rbac={})
    runner = AgentRunner(
        project=project, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
        max_wall_seconds=60.0,
    )
    assert runner.workspace.deadline_at is not None
    import time
    assert runner.workspace.deadline_at > time.time()


async def test_runner_no_deadline_when_max_wall_none(tmp_path: Path):
    project = ProjectConfig(project_id="p1", goal="x", agents=[], rbac={})
    runner = AgentRunner(
        project=project, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
        max_wall_seconds=None,
    )
    assert runner.workspace.deadline_at is None
