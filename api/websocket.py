"""WebSocket endpoint — live board broadcast for an active run.

DESIGN §3 / §16. The agent runner posts signals to the workspace; the
workspace's subscriber list fires per signal; this WebSocket bridge
forwards each signal as a JSON message to connected clients.

For Stage 6, a `RunRegistry` (singleton on app.state) maps run_id →
Workspace. The runner registers its workspace at run start and
unregisters on completion. The WebSocket endpoint subscribes to the
workspace's signal stream while the connection is open.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.signals import Signal
from core.workspace import Workspace


@dataclass
class RunRegistry:
    """Maps active run_id → Workspace. The runner registers its workspace
    at run start so the WebSocket can subscribe."""

    runs: dict[str, Workspace] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def register(self, run_id: str, workspace: Workspace) -> None:
        async with self._lock:
            self.runs[run_id] = workspace

    async def unregister(self, run_id: str) -> None:
        async with self._lock:
            self.runs.pop(run_id, None)

    async def get(self, run_id: str) -> Workspace | None:
        async with self._lock:
            return self.runs.get(run_id)


router = APIRouter()


def _signal_to_json(sig: Signal) -> dict[str, Any]:
    return {
        "id": str(sig.id),
        "type": sig.type.value,
        "summary": sig.summary,
        "detail": sig.detail,
        "from_agent": sig.from_agent,
        "from_user": sig.from_user,
        "priority": sig.priority,
        "iteration": sig.iteration,
        "seen_by": list(sig.seen_by),
        "timestamp": sig.timestamp.isoformat(),
    }


@router.websocket("/ws/runs/{run_id}")
async def run_board_ws(websocket: WebSocket, run_id: str) -> None:
    """Stream the run's board signals live.

    Protocol:
      1. On connect: server sends `{"type": "snapshot", "signals": [...]}`
         with the current board state.
      2. Then: per new signal, server sends `{"type": "signal", "signal": {...}}`.
      3. Disconnect closes the subscription.
    """
    registry: RunRegistry = websocket.app.state.run_registry
    workspace = await registry.get(run_id)
    if workspace is None:
        # Run not active — try to serve from on-disk board if available, else close.
        await websocket.accept()
        store = getattr(websocket.app.state, "run_store", None)
        if store is not None:
            board = await store.get_board(run_id)
            if board is not None:
                await websocket.send_json({"type": "snapshot", "signals": board, "live": False})
                await websocket.close(code=1000)
                return
        await websocket.send_json({"type": "error", "message": f"run {run_id!r} not found"})
        await websocket.close(code=1008)
        return

    await websocket.accept()
    queue: asyncio.Queue[Signal] = asyncio.Queue()

    def on_signal(sig: Signal) -> None:
        try:
            queue.put_nowait(sig)
        except asyncio.QueueFull:
            pass

    unsubscribe = workspace.subscribe(on_signal)

    try:
        # Initial snapshot.
        snapshot = [_signal_to_json(s) for s in workspace.board]
        await websocket.send_json({"type": "snapshot", "signals": snapshot, "live": True})

        # Live stream — race queue.get against websocket.receive so the
        # client's disconnect breaks us out of the queue wait.
        recv_task = asyncio.create_task(websocket.receive())
        try:
            while True:
                queue_task = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {queue_task, recv_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if recv_task in done:
                    queue_task.cancel()
                    # Either a real disconnect (raises) or some other client
                    # message we don't care about — close the loop either way.
                    try:
                        recv_task.result()
                    except WebSocketDisconnect:
                        pass
                    break
                sig = queue_task.result()
                await websocket.send_json({"type": "signal", "signal": _signal_to_json(sig)})
        finally:
            if not recv_task.done():
                recv_task.cancel()
                try:
                    await recv_task
                except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
