"""Track 11 — GET /api/runs/{run_id}/workers cognition snapshot.

Tests both code paths:
- Live runs: registry hit; reads workspace + agent state.
- Completed runs: store fallback; reconstructs from tasks.jsonl.
"""
from __future__ import annotations

import json

import pytest

from core.workspace import Task, Workspace


def _register(app, run_id: str, *, with_runner=True) -> tuple[Workspace, object]:
    """Sync registration mirroring tests/api/test_desks.py pattern."""
    ws = Workspace(run_id=run_id, goal="g")
    app.state.run_registry.runs[run_id] = ws

    if with_runner:
        # Minimal stub runner that exposes what the endpoint reads.
        class _StubAgent:
            _current_tool_call = None
            _last_tool_summary = None
            _current_metrics = None

        class _StubAgentCfg:
            def __init__(self, role):
                self.role = role

        class _StubProject:
            agents = [_StubAgentCfg("dev")]

        class _StubRunner:
            project = _StubProject()
            agents = {"dev": _StubAgent()}

        runner = _StubRunner()
        app.state.run_registry.runners[run_id] = runner
        return ws, runner
    return ws, None


# ── Auth gate ──────────────────────────────────────────────────────────────


async def test_workers_requires_bearer(client, write_run):
    write_run("r1")
    r = await client.get("/api/runs/r1/workers")
    assert r.status_code == 401


# ── Live runs ─────────────────────────────────────────────────────────────


async def test_workers_live_run_returns_per_role_snapshot(client, app, auth_header):
    ws, _runner = _register(app, "run_live")
    # Pretend the runner already created the synthetic root + decomposed.
    ws.tasks["root:dev"] = Task(
        task_id="root:dev", story_id="", assigned_to="dev",
        created_by="runner", goal="g", status="IN_PROGRESS",
    )
    ws.task_queues.setdefault("dev", []).append("root:dev")
    ws.add_task(Task(
        task_id="s1", story_id="", parent_task_id="root:dev",
        assigned_to="dev", created_by="dev",
        goal="scaffold routes", acceptance_criteria=["POST /login"],
    ))
    ws.mutate_task("s1", "IN_PROGRESS")

    r = await client.get("/api/runs/run_live/workers", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["live"] is True
    assert len(body["workers"]) == 1
    w = body["workers"][0]
    assert w["role"] == "dev"
    # active_subtask resolved from active_subtask map.
    assert w["active_subtask"] is not None
    assert w["active_subtask"]["id"] == "s1"
    assert w["active_subtask"]["goal"] == "scaffold routes"
    assert w["active_subtask"]["acceptance"] == ["POST /login"]
    # Synthetic root NOT shown as active.
    assert w["active_subtask"]["id"] != "root:dev"
    # subtask_history excludes synthetic root.
    assert all(not e["id"].startswith("root:") for e in w["subtask_history"])
    assert any(e["id"] == "s1" for e in w["subtask_history"])


async def test_workers_live_run_surfaces_in_flight_tool_call(client, app, auth_header):
    """When an agent's _current_tool_call is set, the snapshot reports it."""
    import time

    ws, runner = _register(app, "run_tool")
    agent = runner.agents["dev"]
    agent._current_tool_call = (
        "fs-write", {"path": "src/foo.py", "content": "x" * 500}, time.time() - 2.5,
    )
    agent._last_tool_summary = ("fs-list", "ok ['a.py', 'b.py']", time.time() - 6)

    r = await client.get("/api/runs/run_tool/workers", headers=auth_header)
    body = r.json()
    w = body["workers"][0]

    assert w["current_tool_call"] is not None
    assert w["current_tool_call"]["name"] == "fs-write"
    assert w["current_tool_call"]["elapsed_seconds"] >= 2.0
    # Long arguments are truncated.
    assert len(w["current_tool_call"]["arguments_preview"]) <= 200

    assert w["last_tool_summary"] is not None
    assert w["last_tool_summary"]["name"] == "fs-list"
    assert "['a.py'" in w["last_tool_summary"]["summary"]


async def test_workers_live_run_no_active_subtask_when_only_root(client, app, auth_header):
    """Synthetic root is plumbing — Workers shouldn't claim 'active'."""
    ws, _runner = _register(app, "run_root_only")
    ws.tasks["root:dev"] = Task(
        task_id="root:dev", story_id="", assigned_to="dev",
        created_by="runner", goal="g", status="IN_PROGRESS",
    )
    ws.task_queues.setdefault("dev", []).append("root:dev")
    ws.active_subtask["dev"] = "root:dev"

    r = await client.get("/api/runs/run_root_only/workers", headers=auth_header)
    body = r.json()
    w = body["workers"][0]
    assert w["active_subtask"] is None


# ── Completed run fallback ────────────────────────────────────────────────


async def test_workers_completed_run_falls_back_to_disk(
    client, app, auth_header, write_run, data_root,
):
    """No registry entry → degrade to tasks.jsonl reconstruction.
    `live=false` so the UI can show a "history only" banner."""
    run_dir = write_run("run_done", project_id="p1")
    # Seed tasks.jsonl with one task assigned to dev.
    tasks_path = run_dir / "tasks.jsonl"
    tasks_path.write_text(
        json.dumps({
            "task_id": "t1", "story_id": "", "parent_task_id": None,
            "assigned_to": "dev", "created_by": "architect",
            "created_at_iter": 0, "goal": "build it",
            "acceptance_criteria": [], "out_of_scope": [], "references": [],
            "status": "COMPLETE", "started_at_iter": 0, "completed_at_iter": 1,
            "closed_by_commit": "abc", "blocked_on": [], "cancelled_reason": "",
            "completion_summary": None,
        }) + "\n",
        encoding="utf-8",
    )

    r = await client.get("/api/runs/run_done/workers", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["live"] is False
    assert any(w["role"] == "dev" for w in body["workers"])
    dev = next(w for w in body["workers"] if w["role"] == "dev")
    # Live-only fields are absent on the disk-fallback path.
    assert dev["current_tool_call"] is None
    assert dev["last_tool_summary"] is None
    assert any(e["goal"] == "build it" for e in dev["subtask_history"])


async def test_workers_unknown_run_404(client, auth_header):
    r = await client.get("/api/runs/ghost/workers", headers=auth_header)
    assert r.status_code == 404
