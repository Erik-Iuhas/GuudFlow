"""Tests for Track 2b: no-op completion enforcement.

When an agent finishes without git commits, the runner checks the [COMMIT]
block summary for a rationale. Missing or too-short rationale → synthetic
FAIL signal. A specific rationale (≥10 chars) → clean COMPLETE with the
no_op_reason on the board.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient, MockTurn
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import AgentSpec, Scheduler, SchedulerStatus
from core.signals import SignalType
from core.tool_manifest import RoleRBAC


def _project_1agent(role: str = "dev") -> ProjectConfig:
    return ProjectConfig(
        project_id="p_noop",
        goal="review and complete",
        agents=[AgentConfig(role=role, description="test agent", depends_on=[])],
        rbac={role: RoleRBAC(role=role, allowed_scopes=[])},
        max_iterations=2,
    )


async def _run_single_agent(
    tmp_path: Path,
    llm_turns: list[MockTurn],
    role: str = "dev",
) -> AgentRunner:
    """Build a runner, drive the single agent through the scheduler, return runner."""
    project = _project_1agent(role)
    llm = MockLLMClient(llm_turns)
    runner = AgentRunner(
        project=project,
        run_id="r_noop",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    specs = [
        AgentSpec(
            role=role,
            runner=runner._make_scheduler_runner(runner.agents[role], project.agents[0]),
            depends_on=[],
        )
    ]
    scheduler = Scheduler(specs, runner.workspace, scope="pipeline")
    await scheduler.run()
    return runner


# ── Case 1: no commits + no rationale → synthetic FAIL ─────────────────────


async def test_no_op_missing_rationale_triggers_fail(tmp_path: Path):
    """Agent ends with [COMMIT] summary too short (<10 chars) → FAILED signal."""
    # The LLM returns a COMMIT block with a vague summary — no actual files written.
    llm_turns = [
        MockTurn(text="[COMMIT]\n  summary: done\n  files: (none)"),
    ]
    runner = await _run_single_agent(tmp_path, llm_turns)

    types = [s.type for s in runner.workspace.board]
    assert SignalType.FAILED in types, (
        "Expected FAILED signal when no-op rationale is missing or too short"
    )
    assert SignalType.COMPLETE not in types


# ── Case 2: no commits + valid rationale → COMPLETE with no_op_reason ───────


async def test_no_op_valid_rationale_completes_cleanly(tmp_path: Path):
    """Agent ends with a specific rationale (≥10 chars) and no file writes → COMPLETE."""
    summary = "no schema drift; tests already cover this case"
    llm_turns = [
        MockTurn(text=f"[COMMIT]\n  summary: {summary}\n  files: (none)"),
    ]
    runner = await _run_single_agent(tmp_path, llm_turns)

    types = [s.type for s in runner.workspace.board]
    assert SignalType.COMPLETE in types, (
        "Expected COMPLETE signal when no-op rationale is specific and long enough"
    )
    assert SignalType.FAILED not in types

    # The no_op_reason should appear on the board as a PROTOCOL signal.
    protocol_signals = [s for s in runner.workspace.board if s.type is SignalType.PROTOCOL]
    assert any(
        s.detail and "no_op_reason:" in s.detail and "drift" in s.detail
        for s in protocol_signals
    ), "Expected a PROTOCOL signal with no_op_reason containing the rationale"


# ── Case 3: actual commits + any rationale → normal COMPLETE ────────────────


async def test_with_real_commits_no_op_check_skipped(tmp_path: Path):
    """When the agent writes a file and commits, the no-op check is bypassed
    and the run completes normally regardless of summary length."""
    from core.llm import ToolCall

    llm_turns = [
        MockTurn(tool_calls=[ToolCall(
            id="t1",
            name="fs-write",
            arguments={"path": "hello.py", "content": "x = 1\n"},
        )]),
        MockTurn(text="[COMMIT]\n  summary: add hello.py\n  files: hello.py"),
    ]
    project = ProjectConfig(
        project_id="p_real",
        goal="write a file",
        agents=[AgentConfig(
            role="dev",
            description="writes a file",
            depends_on=[],
            declared_local_tools=["fs-write"],
        )],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=["fs-write"])},
        max_iterations=2,
    )
    llm = MockLLMClient(llm_turns)
    runner = AgentRunner(
        project=project,
        run_id="r_real",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    specs = [AgentSpec(
        role="dev",
        runner=runner._make_scheduler_runner(runner.agents["dev"], project.agents[0]),
        depends_on=[],
    )]
    scheduler = Scheduler(specs, runner.workspace, scope="pipeline")
    await scheduler.run()

    types = [s.type for s in runner.workspace.board]
    assert SignalType.COMPLETE in types
    assert SignalType.FAILED not in types
