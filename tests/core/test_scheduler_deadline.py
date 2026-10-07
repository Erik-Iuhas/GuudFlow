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


# ── soft time limit (plan §11.6) ───────────────────────────────────────────

async def test_soft_limit_finishes_running_work_and_starts_nothing_new():
    import asyncio
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    ws.deadline_at = time.time() + 0.2
    ws.deadline_mode = "soft"
    started: list[str] = []

    def agent(role: str, delay: float) -> AgentSpec:
        async def runner(w: Workspace) -> None:
            started.append(role)
            await asyncio.sleep(delay)
            w.completed_agents.append(role)
            w.agent_states[role] = AgentState.COMPLETE
            w.post_signal(Signal(type=SignalType.COMPLETE, summary="x", from_agent=role))
        return AgentSpec(role=role, runner=runner)

    later = agent("b", 0.01)
    later.depends_on = ["a"]
    sched = Scheduler([agent("a", 0.5), later], ws)
    result = await sched.run()
    assert result.status is SchedulerStatus.TIME_LIMIT
    assert started == ["a"] and "a" in ws.completed_agents     # a finished, b never started
    assert any(s.type is SignalType.TIME_LIMIT_REACHED for s in ws.board)


async def test_soft_limit_drain_filter_lets_chosen_roles_start():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    ws.deadline_at = time.time() - 1
    ws.deadline_mode = "soft"
    sched = Scheduler([_slow_agent("reviewer", 0.01), _slow_agent("builder", 0.01)], ws)
    sched.drain_filter = lambda role: role == "reviewer"
    result = await sched.run()
    assert result.status is SchedulerStatus.TIME_LIMIT
    assert ws.completed_agents == ["reviewer"]


async def test_soft_limit_does_not_count_time_paused_for_a_human():
    from core.workspace import HumanRequest
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    ws.deadline_mode = "soft"
    ws.deadline_at = time.time() + 30
    sched = Scheduler([_slow_agent("a", 0.01)], ws)
    ws.pending_human = HumanRequest(from_agent="a", iteration=0, question="?")
    sched._account_pause()                               # noqa: SLF001 — pause starts
    sched._paused_since -= 100                           # noqa: SLF001 — 100 s pass paused
    assert not sched._deadline_exceeded()                # noqa: SLF001 — clock stopped
    ws.pending_human = None
    before = ws.deadline_at
    sched._account_pause()                               # noqa: SLF001 — pause ends
    assert ws.deadline_at == pytest.approx(before + 100, abs=1)


async def test_hard_limit_still_stops_on_time():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    ws.deadline_at = time.time() - 1
    ws.deadline_mode = "hard"
    assert (await Scheduler([_slow_agent("a")], ws).run()).status is SchedulerStatus.DEADLINE_EXCEEDED
