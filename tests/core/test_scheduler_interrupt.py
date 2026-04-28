"""Tests for M1 interrupt/resume API and WAITING_FOR_PEER / SAT_OUT state machine.

Covers:
  - interrupt() sets correct AgentState and returns an InterruptToken
  - resume() clears state, queues pending_input, wakes the loop
  - drain_pending_input() returns and clears queued values
  - mark_sat_out() counts as complete for downstream depends_on
  - An agent WAITING_FOR_PEER is skipped by _ready_to_spawn
  - After resume(), an agent re-spawns and can complete
  - SAT_OUT agent unblocks a dependent just like COMPLETE
  - All three interrupt kinds: "human", "peer", "skip"
"""
from __future__ import annotations

import asyncio
import time

import pytest

from core.scheduler import (
    AgentSpec,
    InterruptToken,
    Scheduler,
    SchedulerStatus,
    WaitingForHuman,
    WaitingForPeer,
    WaitingForSkipConfirm,
)
from core.signals import Signal, SignalType
from core.workspace import AgentState, Workspace


# ── Helpers ─────────────────────────────────────────────────────────────────


def _completing_agent(role: str, depends_on: list[str] | None = None) -> AgentSpec:
    async def runner(ws: Workspace) -> None:
        ws.completed_agents.append(role)
        ws.agent_states[role] = AgentState.COMPLETE

    return AgentSpec(role=role, runner=runner, depends_on=depends_on or [])


def _make_ws() -> Workspace:
    return Workspace(run_id="r-interrupt", goal="test", max_iterations=5)


# ── interrupt() state transitions ────────────────────────────────────────────


def test_interrupt_human_sets_state():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    token = sched.interrupt("A", "human", WaitingForHuman(kind="human", question="Which env?"))
    assert ws.agent_states["A"] == AgentState.WAITING_FOR_HUMAN
    assert token.agent_role == "A"
    assert token.kind == "human"


def test_interrupt_peer_sets_state():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    payload = WaitingForPeer(
        target_role="tech_lead",
        ask_id="ask-001",
        deadline_ts=time.time() + 60,
    )
    token = sched.interrupt("A", "peer", payload)
    assert ws.agent_states["A"] == AgentState.WAITING_FOR_PEER
    assert token.id == "ask-001"


def test_interrupt_skip_sets_state():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    payload = WaitingForSkipConfirm(
        confirmer_role="tech_lead",
        skip_id="skip-42",
        deadline_ts=time.time() + 30,
    )
    token = sched.interrupt("A", "skip", payload)
    assert ws.agent_states["A"] == AgentState.WAITING_FOR_SKIP_CONFIRM
    assert token.id == "skip-42"


# ── resume() clears state and queues input ───────────────────────────────────


def test_resume_clears_interrupt_state():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    token = sched.interrupt("A", "peer", WaitingForPeer(ask_id="x", target_role="lead"))
    assert ws.agent_states["A"] == AgentState.WAITING_FOR_PEER

    sched.resume(token, value="the answer")
    assert ws.agent_states["A"] == AgentState.READY
    assert "A" not in sched._interrupt_payloads
    assert "A" not in sched._interrupt_tokens


def test_resume_queues_value():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    token = sched.interrupt("A", "peer", WaitingForPeer(ask_id="x", target_role="lead"))
    sched.resume(token, value={"answer": "yes"})

    items = sched.drain_pending_input("A")
    assert items == [{"answer": "yes"}]


def test_resume_none_value_queues_nothing():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    token = sched.interrupt("A", "human", WaitingForHuman())
    sched.resume(token)

    items = sched.drain_pending_input("A")
    assert items == []


def test_drain_clears_queue():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    token = sched.interrupt("A", "peer", WaitingForPeer(ask_id="y", target_role="lead"))
    sched.resume(token, value="first")
    sched.drain_pending_input("A")
    assert sched.drain_pending_input("A") == []


# ── _ready_to_spawn skips WAITING agents ─────────────────────────────────────


def test_waiting_agent_not_spawnable():
    ws = _make_ws()
    spec = _completing_agent("A")
    sched = Scheduler([spec], ws)
    sched.interrupt("A", "peer", WaitingForPeer(ask_id="z", target_role="lead"))
    ready = sched._ready_to_spawn()
    assert spec not in ready


# ── mark_sat_out counts as complete ──────────────────────────────────────────


def test_mark_sat_out_sets_state():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    sched.mark_sat_out("A")
    assert ws.agent_states["A"] == AgentState.SAT_OUT
    assert "A" in sched._sat_out


def test_sat_out_counts_as_effective_complete():
    ws = _make_ws()
    sched = Scheduler([_completing_agent("A")], ws)
    sched.mark_sat_out("A")
    assert sched._effective_complete("A") is True


def test_sat_out_unblocks_dependent():
    ws = _make_ws()
    a = _completing_agent("A")
    b = _completing_agent("B", depends_on=["A"])
    sched = Scheduler([a, b], ws)
    sched.mark_sat_out("A")
    ready = sched._ready_to_spawn()
    assert any(s.role == "B" for s in ready)


# ── Full async runs ───────────────────────────────────────────────────────────


async def test_interrupt_resume_human_completes():
    """Agent pauses via interrupt() for human, resumes via resume(), then completes."""
    ws = _make_ws()
    first_run = True
    token_ref: list[InterruptToken] = []

    async def controlled_runner(ws: Workspace) -> None:
        nonlocal first_run
        if first_run:
            first_run = False
            tok = sched.interrupt("A", "human", WaitingForHuman(question="Ready?"))
            token_ref.append(tok)
            return
        ws.completed_agents.append("A")
        ws.agent_states["A"] = AgentState.COMPLETE

    spec = AgentSpec(role="A", runner=controlled_runner)
    sched = Scheduler([spec], ws)

    async def _resume_after_delay():
        await asyncio.sleep(0.02)
        sched.resume(token_ref[0], value="yes")

    asyncio.create_task(_resume_after_delay())
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert "A" in ws.completed_agents


async def test_interrupt_resume_peer_then_complete():
    """Developer asks a peer, peer resumes with answer, developer then completes."""
    ws = _make_ws()
    ask_answered: list[str] = []
    token_ref: list[InterruptToken] = []
    first_run = True

    async def dev_runner(ws: Workspace) -> None:
        nonlocal first_run
        if first_run:
            first_run = False
            tok = sched.interrupt(
                "developer", "peer",
                WaitingForPeer(target_role="tech_lead", ask_id="ask-99"),
            )
            token_ref.append(tok)
            return

        # Resume path: drain the answer
        answers = sched.drain_pending_input("developer")
        ask_answered.extend(answers)
        ws.completed_agents.append("developer")
        ws.agent_states["developer"] = AgentState.COMPLETE

    dev = AgentSpec(role="developer", runner=dev_runner)
    sched = Scheduler([dev], ws)

    async def _answer_after_delay():
        await asyncio.sleep(0.02)
        sched.resume(token_ref[0], value="Use the v2 endpoint")

    asyncio.create_task(_answer_after_delay())
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert ask_answered == ["Use the v2 endpoint"]


async def test_sat_out_end_to_end():
    """B mark_sat_out → C (depends on B) starts and completes."""
    ws = _make_ws()
    a_spec = _completing_agent("A")

    b_started = False
    token_ref: list[InterruptToken] = []

    async def b_runner(ws: Workspace) -> None:
        nonlocal b_started
        b_started = True
        # B decides to sat-out immediately
        sched.mark_sat_out("B")

    b_spec = AgentSpec(role="B", runner=b_runner, depends_on=["A"])
    c_spec = _completing_agent("C", depends_on=["B"])

    sched = Scheduler([a_spec, b_spec, c_spec], ws)
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert "B" in result.sat_out
    assert "C" in result.completed


def test_transition_rule_blocks_non_target_after_ask():
    """After ASK posted on the board, only the target role may spawn (M1 §10)."""
    from core.signals import Signal, SignalType
    ws = _make_ws()
    a = _completing_agent("A")
    b = _completing_agent("B")
    sched = Scheduler([a, b], ws)
    # ASKs flow as AGENT_MESSAGE with summary "ASK <from> → <to>".
    ws.post_signal(Signal(
        type=SignalType.AGENT_MESSAGE,
        summary="ASK A → B",
        from_agent="A",
        detail="to=B",
    ))
    ready = sched._ready_to_spawn()
    # Only B should be spawnable (A may not speak after its own ASK).
    assert {s.role for s in ready} == {"B"}


def test_transition_rule_after_rejected_only_author_speaks():
    """After REJECTED with target_role=X, only X may spawn next (M1 §10)."""
    from core.signals import Signal, SignalType
    ws = _make_ws()
    dev = _completing_agent("dev")
    other = _completing_agent("other")
    sched = Scheduler([dev, other], ws)
    ws.post_signal(Signal(
        type=SignalType.REJECTED,
        summary="REJECTED dev",
        from_agent="reviewer",
        detail="target_role=dev",
    ))
    ready = sched._ready_to_spawn()
    assert {s.role for s in ready} == {"dev"}


async def test_peer_deadline_emits_timeout_and_clears_interrupt():
    """A WaitingForPeer past its deadline_ts is reaped: PEER_TIMEOUT signal
    posted, interrupt cleared, role released back to READY."""
    from core.signals import SignalType
    ws = _make_ws()
    spec = _completing_agent("A")
    sched = Scheduler([spec], ws)

    # Park A on a peer interrupt with a deadline already in the past.
    sched.interrupt(
        "A", "peer",
        WaitingForPeer(target_role="lead", ask_id="x", deadline_ts=time.monotonic() - 1),
    )
    assert ws.agent_states["A"] == AgentState.WAITING_FOR_PEER

    sched._reap_expired_interrupts()

    timeouts = [s for s in ws.board if s.type is SignalType.PEER_TIMEOUT]
    assert len(timeouts) == 1
    assert "A" not in sched._interrupt_payloads
    assert ws.agent_states["A"] == AgentState.READY


async def test_skip_deadline_escalates_to_human_not_auto_grant():
    """A WaitingForSkipConfirm past its deadline_ts triggers HUMAN_NEEDED
    (default-deny per M1 §7a). It must NOT auto-mark SAT_OUT."""
    from core.signals import SignalType
    ws = _make_ws()
    spec = _completing_agent("A")
    sched = Scheduler([spec], ws)

    sched.interrupt(
        "A", "skip",
        WaitingForSkipConfirm(
            confirmer_role="tech_lead", skip_id="s-1",
            deadline_ts=time.monotonic() - 1,
        ),
    )

    sched._reap_expired_interrupts()

    timeouts = [s for s in ws.board if s.type is SignalType.SKIP_TIMEOUT]
    assert len(timeouts) == 1
    assert "A" not in sched._sat_out  # NOT auto-granted
    assert ws.pending_human is not None
    assert ws.agent_states["A"] == AgentState.WAITING_FOR_HUMAN


async def test_multiple_waiting_then_all_resume():
    """Two agents interrupt simultaneously; both resume → both complete."""
    ws = _make_ws()
    tokens: list[InterruptToken] = []
    first_runs: dict[str, bool] = {"X": True, "Y": True}

    def make_runner(role: str) -> AgentSpec:
        async def runner(ws: Workspace) -> None:
            if first_runs[role]:
                first_runs[role] = False
                tok = sched.interrupt(
                    role, "peer",
                    WaitingForPeer(target_role="tech_lead", ask_id=f"ask-{role}"),
                )
                tokens.append(tok)
                return
            ws.completed_agents.append(role)
            ws.agent_states[role] = AgentState.COMPLETE

        return AgentSpec(role=role, runner=runner)

    x_spec = make_runner("X")
    y_spec = make_runner("Y")
    sched = Scheduler([x_spec, y_spec], ws)

    async def _resume_both():
        await asyncio.sleep(0.03)
        for tok in tokens:
            sched.resume(tok, value="answer")

    asyncio.create_task(_resume_both())
    result = await sched.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert set(result.completed) == {"X", "Y"}
