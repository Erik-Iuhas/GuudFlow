"""GET /api/runs/{run_id}/desks — M3.5 Chunk 8.

Tests both code paths:
- Live runs: registry hit; reads from in-memory workspace.
- Completed runs: store fallback; reads `tasks.jsonl` from disk.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.workspace import Task, TaskRef, Workspace
from worker.job_runner import serialise_task


def _register_workspace(app, run_id: str) -> Workspace:
    """Sync registration. Same pattern as tests/api/test_runs.py."""
    ws = Workspace(run_id=run_id, goal="g")
    app.state.run_registry.runs[run_id] = ws
    return ws


# ── Auth gate ──────────────────────────────────────────────────────────────


async def test_desks_requires_bearer(client, write_run):
    write_run("run_1")
    r = await client.get("/api/runs/run_1/desks")
    assert r.status_code == 401


# ── Live run: in-memory ────────────────────────────────────────────────────


async def test_desks_live_run_returns_in_memory_state(client, app, auth_header):
    ws = _register_workspace(app, "run_live")
    ws.add_task(Task(
        task_id="t1", story_id="s", assigned_to="developer",
        created_by="architect", goal="ship issuer",
        acceptance_criteria=["rs256"],
        references=[TaskRef(kind="file", value="docs/auth.md", note="design")],
    ))
    ws.add_task(Task(
        task_id="t2", story_id="s", assigned_to="developer", goal="verifier",
    ))
    ws.mutate_task("t1", "IN_PROGRESS")

    r = await client.get("/api/runs/run_live/desks", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["desks"]) == {"developer"}
    assert [t["task_id"] for t in body["desks"]["developer"]] == ["t1", "t2"]
    assert body["tasks_by_id"]["t1"]["status"] == "IN_PROGRESS"
    assert body["tasks_by_id"]["t1"]["acceptance_criteria"] == ["rs256"]
    assert body["tasks_by_id"]["t1"]["references"][0]["kind"] == "file"


async def test_desks_live_run_with_no_tasks_returns_empty(client, app, auth_header):
    _register_workspace(app, "run_live")
    r = await client.get("/api/runs/run_live/desks", headers=auth_header)
    assert r.status_code == 200
    assert r.json() == {"desks": {}, "tasks_by_id": {}}


# ── Completed run: disk fallback ───────────────────────────────────────────


async def test_desks_completed_run_loads_from_disk(client, write_run, auth_header):
    run_dir = write_run("run_done")
    # Plant a tasks.jsonl on disk as if a finished run had persisted.
    tasks_path = run_dir / "tasks.jsonl"
    rows = [
        serialise_task(Task(
            task_id="t1", story_id="s", assigned_to="dev",
            created_by="arch", goal="alpha",
        )),
        # Mutation: status flip
        {**serialise_task(Task(
            task_id="t1", story_id="s", assigned_to="dev",
            created_by="arch", goal="alpha",
        )), "status": "COMPLETE", "closed_by_commit": "abc"},
    ]
    tasks_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )

    r = await client.get("/api/runs/run_done/desks", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tasks_by_id"]["t1"]["status"] == "COMPLETE"
    assert body["tasks_by_id"]["t1"]["closed_by_commit"] == "abc"


async def test_desks_completed_run_with_no_tasks_jsonl(client, write_run, auth_header):
    """A run that finished before M3.5 — no tasks.jsonl on disk.
    The endpoint returns an empty payload, not 404."""
    write_run("run_legacy")
    r = await client.get("/api/runs/run_legacy/desks", headers=auth_header)
    assert r.status_code == 200
    assert r.json() == {"desks": {}, "tasks_by_id": {}}


async def test_desks_unknown_run_returns_404(client, auth_header):
    r = await client.get("/api/runs/ghost/desks", headers=auth_header)
    assert r.status_code == 404


# ── WebSocket streaming (existing transport, no new code) ──────────────────


async def test_task_signals_stream_via_existing_websocket(app):
    """Sanity check: TASK_* signals are SignalType members, so they
    flow through the existing /ws/runs/{run_id} machinery without any
    new WS handler. Verify by subscribing to the workspace directly —
    the WebSocket bridge uses the same `subscribe()` API."""
    from core.signals import SignalType
    from core.workspace import Task, Workspace

    ws = Workspace(run_id="r1", goal="g")
    received: list = []
    ws.subscribe(lambda sig: received.append(sig))
    ws.add_task(Task(task_id="t1", story_id="s", assigned_to="dev", goal="x"))
    types = [s.type for s in received]
    assert SignalType.TASK_ASSIGNED in types
