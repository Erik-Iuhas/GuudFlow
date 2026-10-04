"""Scheduler primitives for the opencode harness (plan §4.4–§4.6, task O11).

Review edges, blocked re-arm, blocked-on cycles, parking, parallelism cap and
switchable transition rules. The six original scheduler tests live in
test_scheduler.py and must keep passing unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from core.scheduler import AgentSpec, Scheduler, SchedulerStatus
from core.signals import Signal, SignalType
from core.workspace import AgentState, Workspace

Runner = Callable[[Workspace], Awaitable[None]]


def _ws(max_iterations: int = 10) -> Workspace:
    return Workspace(run_id="r", goal="g", max_iterations=max_iterations)


def _complete(ws: Workspace, role: str) -> None:
    ws.completed_agents.append(role)
    ws.agent_states[role] = AgentState.COMPLETE


async def test_review_edge_loops_author_then_reviewer() -> None:
    ws = _ws()
    log: list[str] = []
    verdicts = iter(["fail", "pass"])
    holder: dict[str, Scheduler] = {}

    async def builder(w: Workspace) -> None:
        log.append(f"builder{holder['s'].drain_pending_input('builder')}")
        _complete(w, "builder")

    async def reviewer(w: Workspace) -> None:
        verdict = next(verdicts)
        log.append(f"reviewer:{verdict}")
        if verdict == "fail":
            assert holder["s"].request_rework("reviewer", ["builder"], ["fix a.py"])
            return
        _complete(w, "reviewer")

    s = Scheduler([AgentSpec("builder", builder),
                   AgentSpec("reviewer", reviewer, depends_on=["builder"])],
                  ws, transition_rules=False)
    holder["s"] = s
    result = await s.run()
    assert result.status is SchedulerStatus.COMPLETE
    assert log == ["builder[]", "reviewer:fail", "builder[['fix a.py']]", "reviewer:pass"]
    assert s.review_rounds("reviewer", "builder") == 1


async def test_review_rounds_exhausted_returns_false() -> None:
    ws = _ws()
    holder: dict[str, Scheduler] = {}
    outcomes: list[bool] = []

    async def builder(w: Workspace) -> None:
        _complete(w, "builder")

    async def reviewer(w: Workspace) -> None:
        ok = holder["s"].request_rework("reviewer", ["builder"])
        outcomes.append(ok)
        if not ok:  # escalation path: the caller gives up on further rework
            _complete(w, "reviewer")

    s = Scheduler([AgentSpec("builder", builder),
                   AgentSpec("reviewer", reviewer, depends_on=["builder"], max_review_rounds=2)],
                  ws, transition_rules=False)
    holder["s"] = s
    assert (await s.run()).status is SchedulerStatus.COMPLETE
    assert outcomes == [True, True, False]


async def test_block_rearms_completed_upstream_and_resumes() -> None:
    ws = _ws()
    holder: dict[str, Scheduler] = {}
    log: list[str] = []
    planner_runs = 0
    builder_runs = 0

    async def planner(w: Workspace) -> None:
        nonlocal planner_runs
        planner_runs += 1
        log.append(f"planner{holder['s'].drain_pending_input('planner')}")
        _complete(w, "planner")

    async def builder(w: Workspace) -> None:
        nonlocal builder_runs
        builder_runs += 1
        if builder_runs == 1:
            outcome = holder["s"].block("builder", "planner", "which DB?")
            log.append(f"block:{outcome.action}")
            return
        log.append("builder done")
        _complete(w, "builder")

    s = Scheduler([AgentSpec("planner", planner),
                   AgentSpec("builder", builder, depends_on=["planner"])],
                  ws, transition_rules=False)
    holder["s"] = s
    assert (await s.run()).status is SchedulerStatus.COMPLETE
    assert log == ["planner[]", "block:rearmed", "planner['which DB?']", "builder done"]
    assert s.blocked_on("builder") is None


async def test_block_on_running_role_waits_without_rearm() -> None:
    ws = _ws()
    holder: dict[str, Scheduler] = {}
    release = asyncio.Event()
    log: list[str] = []
    a_runs = 0

    async def slow(w: Workspace) -> None:
        await release.wait()
        log.append(f"slow{holder['s'].drain_pending_input('slow')}")
        _complete(w, "slow")

    async def a(w: Workspace) -> None:
        nonlocal a_runs
        a_runs += 1
        if a_runs == 1:
            log.append(holder["s"].block("a", "slow", "need X").action)
            release.set()
            return
        log.append("a done")
        _complete(w, "a")

    s = Scheduler([AgentSpec("slow", slow), AgentSpec("a", a)], ws, transition_rules=False)
    holder["s"] = s
    assert (await s.run()).status is SchedulerStatus.COMPLETE
    assert log == ["waiting", "slow['need X']", "a done"]


async def test_blocked_cycle_parks_on_human() -> None:
    ws = _ws()
    holder: dict[str, Scheduler] = {}
    gate = asyncio.Event()

    async def a(w: Workspace) -> None:
        holder["s"].block("a", "b", None)
        gate.set()

    async def b(w: Workspace) -> None:
        await gate.wait()
        outcome = holder["s"].block("b", "a", None)
        holder["cycle"] = outcome  # type: ignore[assignment]

    s = Scheduler([AgentSpec("a", a), AgentSpec("b", b)], ws, transition_rules=False)
    holder["s"] = s
    run = asyncio.create_task(s.run())
    for _ in range(100):
        if ws.pending_human is not None:
            break
        await asyncio.sleep(0.01)
    assert ws.pending_human is not None and "blocked on each other" in ws.pending_human.question
    assert holder["cycle"].action == "cycle"  # type: ignore[attr-defined]
    await s.force_cancel()
    assert (await run).status is SchedulerStatus.STOPPED


async def test_unknown_block_target() -> None:
    ws = _ws()
    s = Scheduler([AgentSpec("a", lambda w: asyncio.sleep(0))], ws)
    assert s.block("a", "ghost").action == "unknown_role"
    assert s.block("a", "a").action == "unknown_role"


async def test_park_until_woken() -> None:
    ws = _ws()
    holder: dict[str, Scheduler] = {}
    runs: list[str] = []
    ready = asyncio.Event()

    async def worker(w: Workspace) -> None:
        runs.append("worker")
        if not ready.is_set():
            holder["s"].park("worker")
            return
        _complete(w, "worker")

    async def feeder(w: Workspace) -> None:
        await asyncio.sleep(0.05)
        ready.set()
        holder["s"].wake_parked()
        _complete(w, "feeder")

    s = Scheduler([AgentSpec("worker", worker), AgentSpec("feeder", feeder)], ws,
                  transition_rules=False)
    holder["s"] = s
    assert (await s.run()).status is SchedulerStatus.COMPLETE
    assert runs == ["worker", "worker"], "a parked role is not respawned until woken"


async def test_parked_with_nothing_running_is_stuck() -> None:
    ws = _ws()
    holder: dict[str, Scheduler] = {}

    async def worker(w: Workspace) -> None:
        holder["s"].park("worker")

    s = Scheduler([AgentSpec("worker", worker)], ws, transition_rules=False)
    holder["s"] = s
    assert (await s.run()).status is SchedulerStatus.STUCK


async def test_max_parallel_caps_concurrency() -> None:
    ws = _ws()
    running = 0
    peak = 0

    def make(role: str):
        async def run(w: Workspace) -> None:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            _complete(w, role)
        return run

    roles = ["a", "b", "c", "d", "e"]
    s = Scheduler([AgentSpec(r, make(r)) for r in roles], ws, max_parallel=2)
    assert (await s.run()).status is SchedulerStatus.COMPLETE
    assert peak == 2


async def test_transition_rules_can_be_disabled() -> None:
    ws = _ws()
    ws.post_signal(Signal(type=SignalType.REJECTED, summary="x", from_agent="reviewer",
                          detail="author=someone_else"))
    ran: list[str] = []

    async def a(w: Workspace) -> None:
        ran.append("a")
        _complete(w, "a")

    s = Scheduler([AgentSpec("a", a)], ws, transition_rules=False)
    assert (await s.run()).status is SchedulerStatus.COMPLETE and ran == ["a"]


async def test_has_work_gates_spawns_and_counts_no_idle_spawns() -> None:
    """Task board (§4.6): a role is spawned only while it has a card; idle wake-ups
    cost no spawns; completion stays with whoever settles the board."""
    ws = _ws()
    cards = {"author": ["T1", "T2"], "reviewer": []}
    log: list[str] = []
    holder: dict[str, Scheduler] = {}

    def settle() -> None:
        for role in ("author", "reviewer"):
            if not cards[role] and not (role == "reviewer" and cards["author"]) \
                    and role not in ws.completed_agents:
                _complete(ws, role)

    async def author(w: Workspace) -> None:
        card = cards["author"].pop(0)
        log.append(f"author:{card}")
        cards["reviewer"].append(card)
        settle()

    async def reviewer(w: Workspace) -> None:
        card = cards["reviewer"].pop(0)
        log.append(f"reviewer:{card}")
        settle()

    s = Scheduler([AgentSpec("author", author, has_work=lambda: bool(cards["author"])),
                   AgentSpec("reviewer", reviewer, has_work=lambda: bool(cards["reviewer"]))],
                  ws, transition_rules=False, max_parallel=1)
    holder["s"] = s
    result = await asyncio.wait_for(s.run(), timeout=5)
    assert result.status is SchedulerStatus.COMPLETE
    assert sorted(log) == ["author:T1", "author:T2", "reviewer:T1", "reviewer:T2"]
    assert result.spawn_counts == {"author": 2, "reviewer": 2}


async def test_has_work_false_everywhere_is_stuck_not_a_hang() -> None:
    ws = _ws()

    async def never(w: Workspace) -> None:  # pragma: no cover - never spawned
        raise AssertionError("must not run")

    s = Scheduler([AgentSpec("a", never, has_work=lambda: False)], ws, transition_rules=False)
    result = await asyncio.wait_for(s.run(), timeout=5)
    assert result.status is SchedulerStatus.STUCK and s.is_running("a") is False
