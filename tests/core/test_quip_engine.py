"""Tests for core/quip_engine.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import MockLLMClient, MockTurn
from core.quip_engine import AgentVoice, Quip, QuipEngine
from core.scheduler import SchedulerResult, SchedulerStatus
from core.workspace import Workspace


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(run_id="r1", goal="x", completed_agents=["developer", "reviewer"])


async def test_quip_on_run_complete_persists_to_disk(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([MockTurn(text="ship it")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "quip.json")
    engine.set_voices([AgentVoice(role="developer", name="Aria", character="Focused engineer.")])
    result = SchedulerResult(
        status=SchedulerStatus.COMPLETE,
        completed=["developer", "reviewer"],
        failed=[],
        spawn_counts={"developer": 1, "reviewer": 1},
    )
    await engine.on_run_complete(workspace, result)
    assert (tmp_path / "quip.json").exists()
    data = json.loads((tmp_path / "quip.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["text"] == "ship it"
    assert data[0]["event"] == "run_complete"
    assert data[0]["persona"] == "Aria"


async def test_quip_uses_fast_tier_model(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([MockTurn(text="zip")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_run_complete(workspace, result)
    assert llm.calls[0]["model"] == QuipEngine.FAST_TIER_MODEL


async def test_quip_max_tokens_capped(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([MockTurn(text="x")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_run_complete(workspace, result)
    assert llm.calls[0]["max_tokens"] == QuipEngine.MAX_TOKENS


async def test_quip_failure_does_not_crash(tmp_path: Path, workspace: Workspace):
    """If the LLM call raises, the quip log records the failure
    (as best-effort flavour text) rather than propagating."""
    class BadLLM:
        async def complete(self, **kwargs):
            raise RuntimeError("LLM down")
    engine = QuipEngine(llm=BadLLM(), log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_run_complete(workspace, result)
    data = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert "failed" in data[0]["text"].lower()


async def test_on_event_appends_quip(tmp_path: Path):
    llm = MockLLMClient([MockTurn(text="zinger")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    await engine.on_event("merge conflict on shared.py", event="conflict")
    data = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert data[0]["event"] == "conflict"


async def test_quips_accumulate(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([
        MockTurn(text="a"), MockTurn(text="b"), MockTurn(text="c"),
    ])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_iteration_complete(workspace, iteration=1)
    await engine.on_iteration_complete(workspace, iteration=2)
    await engine.on_run_complete(workspace, result)
    data = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert [q["text"] for q in data] == ["a", "b", "c"]


# ── subscribe / unsubscribe (Phase C.2) ────────────────────────────────────


async def test_subscribe_receives_quip_after_persist(tmp_path: Path, workspace: Workspace):
    """Subscribers fire after the quip is appended + persisted to disk —
    so the WebSocket bridge sees a consistent state by the time it
    forwards the event."""
    llm = MockLLMClient([MockTurn(text="great work")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")

    received: list[Quip] = []
    state_at_callback: list[bool] = []

    def on_quip(q: Quip) -> None:
        received.append(q)
        # Verify the on-disk log already includes this quip when the
        # callback fires.
        state_at_callback.append((tmp_path / "q.json").exists())

    engine.subscribe(on_quip)

    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_run_complete(workspace, result)

    assert len(received) == 1
    assert received[0].text == "great work"
    assert received[0].event == "run_complete"
    assert state_at_callback == [True]


async def test_subscribe_fires_on_every_event_type(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([
        MockTurn(text="iter1"), MockTurn(text="event"), MockTurn(text="end"),
    ])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    received: list[Quip] = []
    engine.subscribe(received.append)

    await engine.on_iteration_complete(workspace, iteration=1)
    await engine.on_event("merge conflict", event="conflict")
    await engine.on_run_complete(
        workspace, SchedulerResult(SchedulerStatus.COMPLETE, [], [], {}),
    )

    events = [q.event for q in received]
    assert events == ["iteration_complete", "conflict", "run_complete"]


async def test_unsubscribe_stops_callbacks(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([MockTurn(text="a"), MockTurn(text="b")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    received: list[Quip] = []
    unsubscribe = engine.subscribe(received.append)

    await engine.on_iteration_complete(workspace, iteration=1)
    unsubscribe()
    await engine.on_iteration_complete(workspace, iteration=2)

    assert len(received) == 1
    # But both quips still landed on disk.
    data = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert len(data) == 2


async def test_subscriber_exception_does_not_break_engine(
    tmp_path: Path, workspace: Workspace,
):
    """A buggy subscriber must not prevent other subscribers (or the
    persist path) from working. Same defensive posture as Workspace."""
    llm = MockLLMClient([MockTurn(text="a")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")

    def bad(_q: Quip) -> None:
        raise RuntimeError("boom")

    received: list[Quip] = []
    engine.subscribe(bad)
    engine.subscribe(received.append)

    await engine.on_iteration_complete(workspace, iteration=1)

    # The good subscriber still fired, the persist still happened.
    assert len(received) == 1
    assert (tmp_path / "q.json").exists()


def test_quips_returns_a_copy(tmp_path: Path):
    """Mutating the returned list must not affect engine state."""
    engine = QuipEngine(llm=MockLLMClient([]), log_path=tmp_path / "q.json")
    snapshot = engine.quips()
    snapshot.append(Quip(ts="x", persona="x", role="x", text="x", event="x"))
    assert engine.quips() == []
