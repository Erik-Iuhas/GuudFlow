"""Tests for the scheduler's wall-clock deadline (workspace.deadline_at)."""

from __future__ import annotations

import time

import pytest

from core.scheduler import AgentSpec, Scheduler, SchedulerStatus
from core.signals import Signal, SignalType
from core.workspace import AgentState, Workspace


def _slow_agent(role: str, delay: float = 0.5) -> AgentSpec:
    async def runner(ws: Workspace) -> None:
        import asyncio
        await asyncio.sleep(delay)
        ws.completed_agents.append(role)
        ws.agent_states[role] = AgentState.COMPLETE
        ws.post_signal(Signal(type=SignalType.COMPLETE, summary="x", from_agent=role))
    return AgentSpec(role=role, runner=runner)


async def test_deadline_in_past_returns_deadline_exceeded():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    ws.deadline_at = time.time() - 1  # already past
    sched = Scheduler([_slow_agent("a")], ws)
    result = await sched.run()
    assert result.status is SchedulerStatus.DEADLINE_EXCEEDED


async def test_no_deadline_runs_normally():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    assert ws.deadline_at is None
    sched = Scheduler([_slow_agent("a", delay=0.01)], ws)
    result = await sched.run()
    assert result.status is SchedulerStatus.COMPLETE


async def test_deadline_in_future_does_not_trigger():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    ws.deadline_at = time.time() + 60
    sched = Scheduler([_slow_agent("a", delay=0.01)], ws)
    result = await sched.run()
    assert result.status is SchedulerStatus.COMPLETE
