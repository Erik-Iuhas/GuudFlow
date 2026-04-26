"""Scheduler — the single most load-bearing file in the project.

DESIGN §3.3 makes this the single point of dependency resolution and
concurrency. Every execution pattern (sequential, parallel fan-out, join,
working-group scoping, human pause, max-iteration cutoff) is implemented
entirely here. There is NO `asyncio.gather` outside this module — concurrency
happens here.

The class is reusable. PipelineScheduler and GroupScheduler are the same
class, instantiated at different scopes (different agent collection, different
workspace). The `scope` field is informational; the logic is identical.

Contract for `AgentRunner` callables (Stage 3 — pre-BaseAgent):
- A runner mutates the passed workspace.
- On normal completion: append the role to `workspace.completed_agents`,
  set `workspace.agent_states[role] = AgentState.COMPLETE`, and post any
  COMPLETE/APPROVED signal it wants on the board.
- On hard pause: set `workspace.pending_human = HumanRequest(...)`, set
  `workspace.agent_states[role] = AgentState.WAITING_FOR_HUMAN`, post a
  HUMAN_NEEDED signal, then return. The scheduler will respawn the runner
  after the pause is cleared. The runner sees the cleared `pending_human`
  on the next entry and continues from there.
- On error: raise. The scheduler marks the role failed and continues.

The scheduler never executes work itself — it owns the lifecycle and decides
which runners to invoke when. Runners do the work.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable

from core.workspace import AgentState, Workspace


AgentRunner = Callable[[Workspace], Awaitable[None]]


@dataclass
class AgentSpec:
    """A single agent slot in this scheduler's scope.

    `runner` is the callable that does the work. In Stage 4+ this will be
    `BaseAgent.run`. For Stage 3 we contract directly on the callable so
    the scheduler can be tested without any agent class.
    """

    role: str
    runner: AgentRunner
    depends_on: list[str] = field(default_factory=list)


class SchedulerStatus(str, Enum):
    COMPLETE = "complete"                       # every agent reached completed_agents
    MAX_ITERATIONS = "max_iterations"           # an agent was respawned past the cap
    STUCK = "stuck"                             # nothing spawnable, nothing running, not complete
    DEADLINE_EXCEEDED = "deadline_exceeded"     # workspace.deadline_at passed


@dataclass
class SchedulerResult:
    status: SchedulerStatus
    completed: list[str]
    failed: list[str]
    spawn_counts: dict[str, int]


class Scheduler:
    """Owns the lifecycle of a collection of agents.

    Single instance per scope. The pipeline scheduler operates on the
    pipeline-level agent set; group schedulers (one per group) operate on
    each group's internal agent set. Same class, different inputs.

    Concurrency rules (HARD):
    - This is the ONLY place in the codebase that may call
      `asyncio.create_task` for agent execution.
    - There is no `asyncio.gather` anywhere in this codebase outside
      this file.
    """

    def __init__(
        self,
        agents: list[AgentSpec],
        workspace: Workspace,
        scope: str = "pipeline",
        max_iterations: int | None = None,
    ):
        if len({a.role for a in agents}) != len(agents):
            raise ValueError("duplicate agent roles in scheduler scope")
        self.agents: dict[str, AgentSpec] = {a.role: a for a in agents}
        self.workspace = workspace
        self.scope = scope
        self.max_iterations = (
            max_iterations if max_iterations is not None else workspace.max_iterations
        )

        self._tasks: dict[str, asyncio.Task] = {}
        self._spawn_count: dict[str, int] = defaultdict(int)
        self._failed: set[str] = set()
        self._wake = asyncio.Event()

    # ── Public API ──────────────────────────────────────────────────────────

    def notify(self) -> None:
        """Wake the scheduler to re-evaluate state immediately.

        Call this from outside the scheduler whenever a relevant workspace
        change happens — most importantly, after posting `HUMAN_RESPONDED`
        and clearing `workspace.pending_human`. The Stage 5 runner wraps
        `post_signal` to call this; tests call it explicitly.
        """
        self._wake.set()

    async def run(self) -> SchedulerResult:
        """Drive the scheduler loop until everything is COMPLETE,
        MAX_ITERATIONS triggers, or the run is STUCK."""
        try:
            while True:
                # 0. Wall-clock deadline check — runs before anything else.
                if self._deadline_exceeded():
                    return self._result(SchedulerStatus.DEADLINE_EXCEEDED)

                # 1. Try to spawn anything ready (unless paused for a human).
                if not self.workspace.pending_human:
                    for spec in self._ready_to_spawn():
                        self._spawn(spec)

                # 2. Successful exit — every non-failed agent is complete.
                if self._all_complete():
                    return self._result(SchedulerStatus.COMPLETE)

                # 3. Iteration cap — any role that has been respawned past
                #    the limit without completing forces MAX_ITERATIONS.
                if self._iterations_exceeded():
                    return self._result(SchedulerStatus.MAX_ITERATIONS)

                # 4. Nothing in flight: either parked waiting for a human,
                #    or we have nothing more we could spawn — STUCK.
                if not self._tasks:
                    if self.workspace.pending_human:
                        await self._wake.wait()
                        self._wake.clear()
                        continue
                    return self._result(SchedulerStatus.STUCK)

                # 5. Wait for a task to complete OR an external `notify()`.
                await self._wait_for_change()

                # 6. Reap finished tasks.
                self._reap_done()
        finally:
            await self._cancel_remaining()

    # ── Spawn / reap ───────────────────────────────────────────────────────

    def _ready_to_spawn(self) -> list[AgentSpec]:
        ready: list[AgentSpec] = []
        for role, spec in self.agents.items():
            if role in self._tasks:
                continue
            if role in self.workspace.completed_agents:
                continue
            if role in self._failed:
                continue
            if not all(dep in self.workspace.completed_agents for dep in spec.depends_on):
                continue
            if self._spawn_count[role] >= self.max_iterations:
                continue
            ready.append(spec)
        return ready

    def _spawn(self, spec: AgentSpec) -> None:
        self._spawn_count[spec.role] += 1
        self.workspace.agent_states[spec.role] = AgentState.RUNNING
        self._tasks[spec.role] = asyncio.create_task(
            self._wrap_runner(spec),
            name=f"{self.scope}:{spec.role}",
        )

    async def _wrap_runner(self, spec: AgentSpec) -> None:
        """Run the agent's runner. Always wake the loop on exit so the
        scheduler re-evaluates state regardless of how the runner exited."""
        try:
            await spec.runner(self.workspace)
        finally:
            self._wake.set()

    async def _wait_for_change(self) -> None:
        """Block until any in-flight task finishes OR `notify()` fires."""
        wake_task = asyncio.create_task(self._wake.wait(), name="scheduler:wake")
        waiters: list[asyncio.Task] = [wake_task, *self._tasks.values()]
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._wake.clear()
            if not wake_task.done():
                wake_task.cancel()
                try:
                    await wake_task
                except asyncio.CancelledError:
                    pass

    def _reap_done(self) -> None:
        for role in list(self._tasks.keys()):
            task = self._tasks[role]
            if not task.done():
                continue
            del self._tasks[role]
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                self._failed.add(role)
                self.workspace.agent_states[role] = AgentState.FAILED

    async def _cancel_remaining(self) -> None:
        """Cancel any in-flight tasks at scheduler shutdown."""
        if not self._tasks:
            return
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    # ── Predicates ──────────────────────────────────────────────────────────

    def _all_complete(self) -> bool:
        for role in self.agents:
            if role in self._failed:
                continue
            if role not in self.workspace.completed_agents:
                return False
        return True

    def _deadline_exceeded(self) -> bool:
        deadline = self.workspace.deadline_at
        if deadline is None:
            return False
        return time.time() > deadline

    def _iterations_exceeded(self) -> bool:
        """True if any role has been spawned at the cap without completing.

        We stop the run as a whole when this happens — the alternative is
        leaving the scheduler stuck in STUCK state, which loses information
        for the caller."""
        for role, count in self._spawn_count.items():
            if count >= self.max_iterations:
                if role in self.workspace.completed_agents:
                    continue
                if role in self._failed:
                    continue
                return True
        return False

    def _result(self, status: SchedulerStatus) -> SchedulerResult:
        return SchedulerResult(
            status=status,
            completed=list(self.workspace.completed_agents),
            failed=sorted(self._failed),
            spawn_counts=dict(self._spawn_count),
        )
