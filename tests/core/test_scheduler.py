"""Tests for core/scheduler.py — the Stage 3 GATE.

DESIGN §3.3 / CLAUDE.md make all 6 patterns mandatory before Stage 4 can
begin. This file MUST stay green.

The patterns:
  1. Sequential chain      A → B → C
  2. Parallel fan-out      A → (B + C)         B and C run concurrently
  3. Join                  (A + B) → C         C waits for both
  4. Human pause           HUMAN_NEEDED stops new spawns; running agents finish;
                           HUMAN_RESPONDED resumes — paused agent re-spawns
  5. Group completion      A group's COMPLETE unblocks pipeline-level dependents
                           (implementation: identical to sequential — no special case)
  6. Max iterations        A non-completing agent halts the run cleanly
"""

from __future__ import annotations

import asyncio
import time

import pytest

from core.scheduler import (
    AgentSpec,
    Scheduler,
    SchedulerStatus,
)
from core.signals import Signal, SignalType
from core.workspace import AgentState, HumanRequest, Workspace


# ── Helpers ────────────────────────────────────────────────────────────────


def _completing_agent(
    role: str,
    depends_on: list[str] | None = None,
    delay: float = 0.0,
    on_start: list | None = None,
    on_complete: list | None = None,
) -> AgentSpec:
    """A runner that records timestamps then marks the role complete.

    Optional `on_start` / `on_complete` lists capture timing so tests can
    assert ordering and concurrency precisely.
    """
    async def runner(ws: Workspace) -> None:
        if on_start is not None:
            on_start.append((role, time.perf_counter()))
        if delay:
            await asyncio.sleep(delay)
        ws.completed_agents.append(role)
        ws.agent_states[role] = AgentState.COMPLETE
        ws.post_signal(Signal(
            type=SignalType.COMPLETE,
            summary=f"{role} done",
            from_agent=role,
        ))
        if on_complete is not None:
            on_complete.append((role, time.perf_counter()))

    return AgentSpec(role=role, runner=runner, depends_on=depends_on or [])


def _pausing_agent(
    role: str,
    depends_on: list[str] | None = None,
) -> tuple[AgentSpec, dict]:
    """An agent that pauses for human input the first time it runs and
    completes the second time. Returns the spec + a state dict for tests."""
    state = {"runs": 0}

    async def runner(ws: Workspace) -> None:
        state["runs"] += 1
        if state["runs"] == 1:
            ws.pending_human = HumanRequest(
                from_agent=role,
                question="Need answer",
                iteration=ws.iteration,
            )
            ws.agent_states[role] = AgentState.WAITING_FOR_HUMAN
            ws.post_signal(Signal(
                type=SignalType.HUMAN_NEEDED,
                summary=f"{role} needs human",
                from_agent=role,
            ))
            return
        # Resume path
        ws.completed_agents.append(role)
        ws.agent_states[role] = AgentState.COMPLETE
        ws.post_signal(Signal(
            type=SignalType.COMPLETE,
            summary=f"{role} done",
            from_agent=role,
        ))

    return AgentSpec(role=role, runner=runner, depends_on=depends_on or []), state


def _never_completing_agent(role: str, depends_on: list[str] | None = None) -> AgentSpec:
    async def runner(ws: Workspace) -> None:
        # Returns immediately without completing. Scheduler will respawn
        # until max_iterations triggers.
        return

    return AgentSpec(role=role, runner=runner, depends_on=depends_on or [])


# ── 1. Sequential chain A → B → C ───────────────────────────────────────────


async def test_sequential_chain():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    order: list[str] = []
    starts: list = []
    a = _completing_agent("A", on_start=starts)
    b = _completing_agent("B", depends_on=["A"], on_start=starts)
    c = _completing_agent("C", depends_on=["B"], on_start=starts)

    sched = Scheduler([a, b, c], ws)
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert ws.completed_agents == ["A", "B", "C"]
    assert [r for r, _ in starts] == ["A", "B", "C"]


# ── 2. Parallel fan-out A → (B + C) ─────────────────────────────────────────


async def test_parallel_fan_out():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    starts: list = []
    completes: list = []

    a = _completing_agent("A", on_complete=completes)
    b = _completing_agent("B", depends_on=["A"], delay=0.05, on_start=starts)
    c = _completing_agent("C", depends_on=["A"], delay=0.05, on_start=starts)

    sched = Scheduler([a, b, c], ws)
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert set(ws.completed_agents) == {"A", "B", "C"}

    # B and C must start AFTER A completes, and within a tight window of
    # each other (concurrent, not serialized).
    a_complete_t = next(t for r, t in completes if r == "A")
    b_start_t = next(t for r, t in starts if r == "B")
    c_start_t = next(t for r, t in starts if r == "C")
    assert b_start_t >= a_complete_t
    assert c_start_t >= a_complete_t
    assert abs(b_start_t - c_start_t) < 0.04, (
        f"B and C should start ~simultaneously but were "
        f"{abs(b_start_t - c_start_t):.3f}s apart"
    )


# ── 3. Join (A + B) → C ─────────────────────────────────────────────────────


async def test_join_waits_for_all_dependencies():
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    completes: list = []
    starts: list = []

    a = _completing_agent("A", delay=0.02, on_complete=completes)
    b = _completing_agent("B", delay=0.06, on_complete=completes)
    c = _completing_agent("C", depends_on=["A", "B"], on_start=starts)

    sched = Scheduler([a, b, c], ws)
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert ws.completed_agents[-1] == "C"

    a_done = next(t for r, t in completes if r == "A")
    b_done = next(t for r, t in completes if r == "B")
    c_start = next(t for r, t in starts if r == "C")
    assert c_start >= max(a_done, b_done), "C started before all dependencies complete"


# ── 4. Human pause ──────────────────────────────────────────────────────────


async def test_human_pause_halts_new_spawns_and_resumes_after_response():
    """The rule under test (DESIGN §3.3, §6): when `pending_human` is set,
    NEW spawns are halted; already-running agents finish; once
    `HUMAN_RESPONDED` lands and the scheduler is notified, new spawns
    resume.

    To exercise the rule we need an agent whose dependencies become
    satisfied *during* the pause — otherwise we're not really testing the
    halt, only the dependency rule. Construction:
      A — completes fast (no deps).
      B — pauses on first run (no deps, sets pending_human).
      C — depends on A. Becomes ready as soon as A completes, which
          happens before / during B's pause. C must NOT spawn while
          pending_human is set, even though its deps are satisfied.
    """
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    a = _completing_agent("A")
    b, b_state = _pausing_agent("B")

    c_started = asyncio.Event()
    async def c_runner(workspace: Workspace) -> None:
        c_started.set()
        workspace.completed_agents.append("C")
        workspace.agent_states["C"] = AgentState.COMPLETE
        workspace.post_signal(Signal(
            type=SignalType.COMPLETE, summary="C done", from_agent="C",
        ))
    c = AgentSpec(role="C", runner=c_runner, depends_on=["A"])

    sched = Scheduler([a, b, c], ws)
    run_task = asyncio.create_task(sched.run())

    # Wait for B to pause. The scheduler will park itself awaiting `notify()`.
    for _ in range(200):
        if ws.pending_human is not None:
            break
        await asyncio.sleep(0.005)
    assert ws.pending_human is not None, "B never reached the paused state"

    # A should be complete by now (it never pauses). C is now eligible by
    # dependency, but pending_human is set — C must NOT have spawned.
    # Give the loop a generous window to mis-spawn if the rule is broken.
    await asyncio.sleep(0.05)
    assert "A" in ws.completed_agents
    assert not c_started.is_set(), "C was spawned while pending_human was set"

    # Respond to the human and notify the scheduler.
    ws.pending_human = None
    ws.post_signal(Signal(
        type=SignalType.HUMAN_RESPONDED,
        summary="user replied",
    ))
    sched.notify()

    result = await asyncio.wait_for(run_task, timeout=2.0)
    assert result.status is SchedulerStatus.COMPLETE
    assert {"A", "B", "C"} <= set(ws.completed_agents)
    assert c_started.is_set()
    assert b_state["runs"] == 2  # paused once, resumed once


# ── 5. Group completion ─────────────────────────────────────────────────────


async def test_group_completion_unblocks_pipeline_dependents():
    """A 'group' (modeled as a single role posting COMPLETE) unblocks a
    pipeline-level downstream agent. From the scheduler's POV this is the
    same code path as sequential dependency — there is no special case for
    groups, by design (DESIGN §4b: 'Same logic, different scope. No special
    cases.'). The test asserts that contract."""
    ws = Workspace(run_id="r1", goal="x", max_iterations=5)
    completes: list = []
    starts: list = []

    group = _completing_agent("frontend_group", on_complete=completes)
    downstream = _completing_agent(
        "integration_reviewer",
        depends_on=["frontend_group"],
        on_start=starts,
    )

    sched = Scheduler([group, downstream], ws, scope="pipeline")
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert ws.completed_agents == ["frontend_group", "integration_reviewer"]
    group_done_t = next(t for r, t in completes if r == "frontend_group")
    reviewer_start_t = next(t for r, t in starts if r == "integration_reviewer")
    assert reviewer_start_t >= group_done_t


# ── 6. Max iterations cutoff ────────────────────────────────────────────────


async def test_max_iterations_cutoff_halts_cleanly():
    """An agent that never marks itself complete is respawned each loop.
    Once spawn count hits max_iterations, the scheduler returns
    MAX_ITERATIONS without raising and without leaving tasks running."""
    ws = Workspace(run_id="r1", goal="x", max_iterations=3)
    a = _never_completing_agent("A")

    sched = Scheduler([a], ws)
    result = await sched.run()

    assert result.status is SchedulerStatus.MAX_ITERATIONS
    assert "A" not in ws.completed_agents
    assert result.spawn_counts.get("A") == 3


# ── No-asyncio.gather contract ──────────────────────────────────────────────


def test_no_asyncio_gather_outside_scheduler():
    """DESIGN §3.3 hard rule. Walk core/ and agents/ tree; the only file
    that may contain `asyncio.gather` is core/scheduler.py."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in list(repo.rglob("*.py")):
        rel = path.relative_to(repo).as_posix()
        # Skip our own test file (this very assertion mentions the string),
        # the scheduler implementation, dependencies, virtualenvs, and the
        # progress tracker.
        if rel == "core/scheduler.py":
            continue
        if rel == "tests/core/test_scheduler.py":
            continue
        if any(part.startswith(".") for part in path.relative_to(repo).parts):
            continue
        if "venv" in rel.split("/") or "site-packages" in rel:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "asyncio.gather" in content:
            offenders.append(rel)
    assert offenders == [], f"asyncio.gather found outside core/scheduler.py: {offenders}"
