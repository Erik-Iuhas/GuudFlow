"""Tests for the WebSocket live-board endpoint.

Uses FastAPI's `TestClient` (sync) which is the supported way to test
WebSocket endpoints. We register workspaces by writing directly to
the registry's `runs` dict rather than awaiting `register()` — this
avoids the "event loop in event loop" deadlock that pytest-asyncio's
async-mode pytest causes when a sync test tries to drive an async
coroutine.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.signals import Signal, SignalType
from core.workspace import Workspace


@pytest.fixture
def sync_client(app):
    return TestClient(app)


def _register(app, run_id: str, workspace: Workspace) -> None:
    """Sync registration — bypasses the async lock for test simplicity."""
    app.state.run_registry.runs[run_id] = workspace


def test_websocket_unknown_run_returns_error(sync_client):
    with sync_client.websocket_connect("/ws/runs/ghost") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_websocket_serves_snapshot_from_disk(sync_client, write_run):
    """If the run is no longer active but its board.json is on disk,
    serve a final snapshot then close."""
    write_run("run_done", board=[
        {"type": "STARTED", "summary": "x", "from_agent": "developer"},
    ])
    with sync_client.websocket_connect("/ws/runs/run_done") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert msg["live"] is False
        assert len(msg["signals"]) == 1


def test_websocket_streams_live_signals(sync_client, app):
    ws_obj = Workspace(run_id="run_live", goal="x")
    _register(app, "run_live", ws_obj)

    with sync_client.websocket_connect("/ws/runs/run_live") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["live"] is True
        assert snapshot["signals"] == []

        ws_obj.post_signal(Signal(
            type=SignalType.STARTED,
            summary="dev started",
            from_agent="developer",
        ))
        live = ws.receive_json()
        assert live["type"] == "signal"
        assert live["signal"]["type"] == "STARTED"
        assert live["signal"]["summary"] == "dev started"


def test_websocket_initial_snapshot_includes_existing_board(sync_client, app):
    ws_obj = Workspace(run_id="run_x", goal="x")
    ws_obj.post_signal(Signal(type=SignalType.STARTED, summary="a", from_agent="dev"))
    ws_obj.post_signal(Signal(type=SignalType.COMPLETE, summary="b", from_agent="dev"))
    _register(app, "run_x", ws_obj)

    with sync_client.websocket_connect("/ws/runs/run_x") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert len(snapshot["signals"]) == 2


# ── /ws/runs/{id}/quips (Phase C.2) ────────────────────────────────────────


def _register_quip_engine(app, run_id: str, log_path):
    """Sync registration of a QuipEngine for testing — bypasses the
    async lock, same pattern as _register for Workspaces."""
    from core.llm import MockLLMClient
    from core.quip_engine import QuipEngine
    engine = QuipEngine(llm=MockLLMClient([]), log_path=log_path)
    app.state.quip_registry.engines[run_id] = engine
    return engine


def test_quip_websocket_unknown_run_returns_error(sync_client):
    with sync_client.websocket_connect("/ws/runs/ghost/quips") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_quip_websocket_serves_disk_log_when_engine_unregistered(
    sync_client, data_root, write_run,
):
    """Finished run case: no live engine, but quip_log.json on disk —
    serve as snapshot then close."""
    run_dir = write_run("run_done")
    import json as _json
    quip_data = [
        {"ts": "2026-04-26T10:00:00Z", "persona": "Aria",
         "text": "all done", "event": "run_complete"},
    ]
    (run_dir / "quip_log.json").write_text(
        _json.dumps(quip_data), encoding="utf-8",
    )

    with sync_client.websocket_connect("/ws/runs/run_done/quips") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "snapshot"
        assert msg["live"] is False
        assert msg["quips"] == quip_data


def test_quip_websocket_streams_live_quips(sync_client, app, tmp_path):
    """Engine registered, subscriber fires on new quips → WS forwards
    them with the {"type": "quip", "quip": {...}} envelope."""
    import asyncio as _asyncio
    from core.quip_engine import Quip

    engine = _register_quip_engine(app, "run_live_q", tmp_path / "q.json")

    with sync_client.websocket_connect("/ws/runs/run_live_q/quips") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["live"] is True
        assert snapshot["quips"] == []

        # Manually push a quip through the engine (skip the LLM call).
        q = Quip(
            ts="2026-04-26T10:01:00Z", persona="system", role="system",
            text="live one", event="iteration_complete",
        )
        engine._quips.append(q)
        engine._notify(q)

        live = ws.receive_json()
        assert live["type"] == "quip"
        assert live["quip"]["text"] == "live one"
        assert live["quip"]["event"] == "iteration_complete"


def test_quip_websocket_initial_snapshot_includes_existing_quips(
    sync_client, app, tmp_path,
):
    """If quips have already accumulated when the client connects, the
    initial snapshot includes them."""
    from core.quip_engine import Quip

    engine = _register_quip_engine(app, "run_with_history", tmp_path / "q.json")
    engine._quips.append(Quip(ts="t1", persona="p", role="r", text="early", event="iteration_complete"))
    engine._quips.append(Quip(ts="t2", persona="p", role="r", text="middle", event="iteration_complete"))

    with sync_client.websocket_connect("/ws/runs/run_with_history/quips") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert snapshot["live"] is True
        assert [q["text"] for q in snapshot["quips"]] == ["early", "middle"]
