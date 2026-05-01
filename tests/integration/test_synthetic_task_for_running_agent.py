"""Synthetic task creation for agents with no explicit task assignment.

Verifies that:
  - An agent started without any tasks on its desk gets a synthetic
    IN_PROGRESS task (created_by="scheduler").
  - On agent COMPLETE, the synthetic task is marked COMPLETE.
  - Agents with an existing task do NOT get a second synthetic one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.llm.client import MockTurn
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.workspace import Task


def _project(agents: list[AgentConfig]) -> ProjectConfig:
    return ProjectConfig(
        project_id="p1",
        goal="build the game",
        agents=agents,
        rbac={},
        max_iterations=3,
    )


def _dev_cfg(**overrides) -> AgentConfig:
    base = dict(
        role="core_game_dev",
        description="Implements core game mechanics",
        max_tool_iterations=5,
    )
    base.update(overrides)
    return AgentConfig(**base)


def _commit_turn() -> MockTurn:
    return MockTurn(
        text="[COMMIT]\n  summary: done",
        stop_reason="end_turn",
    )


@pytest.mark.asyncio
async def test_synthetic_task_created_when_desk_empty(tmp_path: Path):
    """Agent with empty desk gets a synthetic IN_PROGRESS task at dispatch."""
    cfg = _dev_cfg()
    llm = MockLLMClient([_commit_turn()] * 5)
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r1",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]

    # Desk is empty before the synthetic task is created.
    assert not runner.workspace.desk_for(cfg.role)

    runner._ensure_synthetic_task(cfg.role, cfg)

    desk = runner.workspace.desk_for(cfg.role)
    assert len(desk) == 1
    t = desk[0]
    assert t.created_by == "scheduler"
    assert t.status == "IN_PROGRESS"
    assert t.goal == cfg.description


@pytest.mark.asyncio
async def test_synthetic_task_not_created_when_desk_has_tasks(tmp_path: Path):
    """Agent with an existing task does NOT get a second synthetic task."""
    cfg = _dev_cfg()
    llm = MockLLMClient([_commit_turn()] * 5)
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r2",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()

    # Pre-populate with an explicit task.
    runner.workspace.add_task(Task(
        task_id="real_task",
        story_id="",
        assigned_to=cfg.role,
        created_by="tech_lead",
        goal="implement the engine",
        status="PENDING",
    ))

    runner._ensure_synthetic_task(cfg.role, cfg)

    desk = runner.workspace.desk_for(cfg.role)
    assert len(desk) == 1
    assert desk[0].task_id == "real_task"


@pytest.mark.asyncio
async def test_close_synthetic_task_marks_complete(tmp_path: Path):
    """_close_synthetic_task transitions the scheduler task to COMPLETE."""
    cfg = _dev_cfg()
    llm = MockLLMClient([])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r3",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    runner._ensure_synthetic_task(cfg.role, cfg)

    desk_before = runner.workspace.desk_for(cfg.role)
    assert desk_before[0].status == "IN_PROGRESS"

    runner._close_synthetic_task(cfg.role)

    desk_after = runner.workspace.desk_for(cfg.role, include_complete=True)
    assert desk_after[0].status == "COMPLETE"


@pytest.mark.asyncio
async def test_close_does_not_affect_real_tasks(tmp_path: Path):
    """_close_synthetic_task does not touch tasks created by planners."""
    cfg = _dev_cfg()
    llm = MockLLMClient([])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r4",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()

    runner.workspace.add_task(Task(
        task_id="real_t",
        story_id="",
        assigned_to=cfg.role,
        created_by="tech_lead",
        goal="do real work",
        status="IN_PROGRESS",
        started_at_iter=0,
    ))

    runner._close_synthetic_task(cfg.role)

    # Real task must remain IN_PROGRESS.
    assert runner.workspace.tasks["real_t"].status == "IN_PROGRESS"
