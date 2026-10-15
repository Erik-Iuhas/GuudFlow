"""drive_turn's stall timeout (plan §11.6): a busy turn runs on, a silent one is aborted."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from core.harness.opencode_runner import drive_turn


class FakeClient:
    def __init__(self, beats: int, gap: float, then_idle: bool) -> None:
        self.beats, self.gap, self.then_idle = beats, gap, then_idle
        self.aborted = False

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "server.connected", "properties": {}}
        for _ in range(self.beats):
            await asyncio.sleep(self.gap)
            yield {"type": "message.part.updated", "properties": {"part": {"type": "text"}}}
        if self.then_idle:
            yield {"type": "session.idle", "properties": {"sessionID": "s"}}
        await asyncio.sleep(3600)

    async def prompt_async(self, *a: Any, **k: Any) -> None:
        return None

    async def abort(self, session_id: str) -> None:
        self.aborted = True


async def test_busy_turn_longer_than_the_stall_window_finishes() -> None:
    client = FakeClient(beats=6, gap=0.1, then_idle=True)          # 0.6 s of steady work
    stats = await drive_turn(client, "s", "hi", timeout=None, stall=0.3)  # type: ignore[arg-type]
    assert not stats.timed_out and not client.aborted


async def test_silent_turn_is_aborted_after_the_stall_window() -> None:
    client = FakeClient(beats=1, gap=0.05, then_idle=False)
    stats = await drive_turn(client, "s", "hi", timeout=None, stall=0.2)  # type: ignore[arg-type]
    assert stats.timed_out and client.aborted
    assert stats.errors[-1] == "turn made no progress for 0.2s and was aborted"


async def test_absolute_cap_still_applies_when_set() -> None:
    client = FakeClient(beats=50, gap=0.05, then_idle=True)         # busy for 2.5 s
    stats = await drive_turn(client, "s", "hi", timeout=0.3, stall=1.0)  # type: ignore[arg-type]
    assert stats.timed_out and "exceeded" in stats.errors[-1]


class RetryingClient(FakeClient):
    """opencode retrying a failing model call: each retry is an event, so no stall."""

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "server.connected", "properties": {}}
        for n in range(1, 20):
            await asyncio.sleep(0.01)
            yield {"type": "session.status", "properties": {"sessionID": "s", "status": {
                "type": "retry", "attempt": n, "message": "upstream 'mistral' unreachable: ConnectError"}}}
        await asyncio.sleep(3600)


async def test_a_model_call_that_keeps_failing_stops_the_turn_with_the_reason(monkeypatch) -> None:
    monkeypatch.setenv("GUUDFLOW_MODEL_RETRIES", "3")
    client = RetryingClient(beats=0, gap=0, then_idle=False)
    stats = await drive_turn(client, "s", "hi", timeout=None, stall=5.0)  # type: ignore[arg-type]
    assert client.aborted and stats.retries == 3 and not stats.timed_out
    assert "model call kept failing (3 tries)" in stats.errors[-1] and "ConnectError" in stats.errors[-1]


async def test_a_turn_ends_as_soon_as_done_says_so() -> None:
    """Seen live: a model kept calling the notebook tool after its notebook was saved, so the
    end of the run waited for it. `done` ends the turn without counting it as a timeout."""
    client = FakeClient(beats=50, gap=0.02, then_idle=False)
    seen: list[dict[str, Any]] = []
    stats = await drive_turn(client, "s", "hi", timeout=None, stall=5.0,  # type: ignore[arg-type]
                             on_event=seen.append, done=lambda: len(seen) >= 4)
    assert client.aborted and not stats.timed_out and not stats.errors
    assert len(seen) == 4
