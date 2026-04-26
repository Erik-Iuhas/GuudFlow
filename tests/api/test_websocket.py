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
