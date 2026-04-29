"""Tests for GET /api/dashboard (M4 Chunk 2)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.websocket import RunRegistry
from core.workspace import AgentState, Workspace


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_workspace(**kwargs) -> Workspace:
    ws = Workspace(run_id="run1", goal="test goal")
    for k, v in kwargs.items():
        object.__setattr__(ws, k, v) if hasattr(ws, k) else setattr(ws, k, v)
    return ws


# ── auth ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_requires_bearer(client):
    resp = await client.get("/api/dashboard")
    assert resp.status_code == 401


# ── empty stores ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_empty_stores(client, auth_header):
    resp = await client.get("/api/dashboard", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_runs"] == []
    assert data["recent_completions"] == []
    assert data["pending_approvals"] == []
    assert data["top_personas"] == []


# ── active runs ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_active_run_task_progress(client, auth_header, app, write_run):
    from core.workspace import Task, TaskStatus

    write_run("run1", project_id="proj1")

    ws = Workspace(run_id="run1", goal="build thing")
    ws.agent_states["developer"] = AgentState.RUNNING
    ws.hired_personas["developer"] = "persona_abc"
    # 3 tasks: 1 complete, 1 in-progress, 1 pending
    for i, status in enumerate(["COMPLETE", "IN_PROGRESS", "PENDING"]):
        t = Task(
            task_id=f"t{i}",
            story_id="s1",
            goal=f"task {i}",
            assigned_to="developer",
            status=status,
        )
        ws.tasks[t.task_id] = t

    app.state.run_registry.runs["run1"] = ws

    resp = await client.get("/api/dashboard", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["active_runs"]) == 1
    run = data["active_runs"][0]
    assert run["run_id"] == "run1"
    assert run["task_progress"] == {"closed": 1, "total": 3}
    assert run["status"] == "running"
    agents = run["current_agents"]
    assert len(agents) == 1
    assert agents[0]["role"] == "developer"
    assert agents[0]["persona_id"] == "persona_abc"


# ── recent completions ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_recent_completions_excludes_active(
    client, auth_header, app, write_run, data_root,
):
    # run1 is active, run2 is completed
    write_run("run1")
    write_run("run2")
    # Overwrite run2 meta to be complete
    meta2 = {"run_id": "run2", "project_id": "proj1", "status": "complete",
              "created_at": "2026-04-28T10:00:00Z"}
    (data_root / "runs" / "run2" / "meta.json").write_text(
        json.dumps(meta2), encoding="utf-8"
    )

    ws1 = Workspace(run_id="run1", goal="active")
    app.state.run_registry.runs["run1"] = ws1

    resp = await client.get("/api/dashboard", headers=auth_header)
    data = resp.json()

    active_ids = {r["run_id"] for r in data["active_runs"]}
    completion_ids = {r["run_id"] for r in data["recent_completions"]}
    assert "run1" in active_ids
    assert "run1" not in completion_ids
    assert "run2" in completion_ids


# ── pending approvals ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_pending_approvals(client, auth_header, app):
    from core.workspace import ApprovalRequest

    ws = Workspace(run_id="run1", goal="test")
    ws.pending_approvals.append(ApprovalRequest(
        id="appr-1",
        kind="heavy_tier_dispatch",
        summary="Spawn heavy-tier tech lead?",
    ))
    app.state.run_registry.runs["run1"] = ws

    resp = await client.get("/api/dashboard", headers=auth_header)
    data = resp.json()
    assert len(data["pending_approvals"]) == 1
    approval = data["pending_approvals"][0]
    assert approval["run_id"] == "run1"
    assert approval["approval_id"] == "appr-1"
    assert approval["kind"] == "heavy_tier_dispatch"


# ── top personas ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_top_personas_sorted_by_activity(
    client, auth_header, write_persona,
):
    write_persona(
        "persona-a",
        name="Alice",
        specialty="backend",
        reputation={"production": {"total_runs": 5, "approved_runs": 4}},
    )
    write_persona(
        "persona-b",
        name="Bob",
        specialty="frontend",
        reputation={"production": {"total_runs": 2, "approved_runs": 1}},
    )

    resp = await client.get("/api/dashboard", headers=auth_header)
    data = resp.json()
    personas = data["top_personas"]
    assert len(personas) >= 2
    # Alice should be first (5 runs > 2 runs)
    assert personas[0]["name"] == "Alice"
    assert personas[1]["name"] == "Bob"
