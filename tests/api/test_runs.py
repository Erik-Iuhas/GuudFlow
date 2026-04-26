"""Tests for /api/runs."""

from __future__ import annotations

from pathlib import Path


async def test_list_runs_empty(client):
    r = await client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_runs_with_data(client, write_run):
    write_run("run_1", project_id="p1")
    write_run("run_2", project_id="p2")
    r = await client.get("/api/runs")
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_list_runs_filtered_by_project(client, write_run):
    write_run("run_1", project_id="p1")
    write_run("run_2", project_id="p2")
    r = await client.get("/api/runs?project_id=p1")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["project_id"] == "p1"


async def test_get_run(client, write_run):
    write_run("run_1", project_id="p1")
    r = await client.get("/api/runs/run_1")
    assert r.status_code == 200
    assert r.json()["run_id"] == "run_1"


async def test_get_unknown_run(client):
    r = await client.get("/api/runs/ghost")
    assert r.status_code == 404


async def test_get_run_board(client, write_run):
    board = [{"type": "STARTED", "summary": "x", "from_agent": "developer"}]
    write_run("run_1", board=board)
    r = await client.get("/api/runs/run_1/board")
    assert r.status_code == 200
    assert r.json() == board


async def test_download_deliverable(client, write_run, data_root: Path):
    run_dir = write_run("run_1")
    (run_dir / "workspace" / "deliverables" / "out.txt").write_text(
        "deliverable contents", encoding="utf-8",
    )
    r = await client.get("/api/runs/run_1/deliverables/out.txt")
    assert r.status_code == 200
    assert r.text == "deliverable contents"


async def test_download_deliverable_path_escape_blocked(client, write_run):
    write_run("run_1")
    r = await client.get("/api/runs/run_1/deliverables/../meta.json")
    # Either 400 (escape detected) or 404 (resolved file not found in dir).
    assert r.status_code in (400, 404)


async def test_download_missing_deliverable(client, write_run):
    write_run("run_1")
    r = await client.get("/api/runs/run_1/deliverables/nope.txt")
    assert r.status_code == 404


# ── POST /api/runs/{id}/comment (Phase C.1) ────────────────────────────────


def _register_workspace(app, run_id: str, goal: str = "x"):
    """Sync registration — bypasses async lock for test simplicity, same
    pattern as tests/api/test_websocket.py."""
    from core.workspace import Workspace
    ws = Workspace(run_id=run_id, goal=goal)
    app.state.run_registry.runs[run_id] = ws
    return ws


async def test_post_comment_to_active_run(client, app, auth_header):
    ws = _register_workspace(app, "run_live")
    r = await client.post(
        "/api/runs/run_live/comment",
        json={"text": "use Tailwind, not raw CSS"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["type"] == "USER_COMMENT"

    # Signal landed on the workspace board AND on user_board (filtered view).
    assert len(ws.board) == 1
    assert ws.board[0].summary == "use Tailwind, not raw CSS"
    assert ws.board[0].from_user is True
    assert ws.board[0].priority == "normal"
    assert len(ws.user_board) == 1


async def test_post_comment_high_priority(client, app, auth_header):
    ws = _register_workspace(app, "run_live")
    r = await client.post(
        "/api/runs/run_live/comment",
        json={"text": "stop, the auth changes are wrong", "priority": "high"},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json()["type"] == "USER_COMMENT_PRIORITY"
    assert ws.board[0].priority == "high"


async def test_post_comment_to_inactive_run_returns_410(client, auth_header):
    """A run that isn't registered (finished or never started) returns
    410 Gone — comments only land mid-run."""
    r = await client.post(
        "/api/runs/run_done/comment",
        json={"text": "too late"},
        headers=auth_header,
    )
    assert r.status_code == 410


async def test_post_comment_requires_auth(client, app):
    _register_workspace(app, "run_live")
    r = await client.post(
        "/api/runs/run_live/comment",
        json={"text": "x"},
    )
    assert r.status_code == 401


async def test_post_comment_rejects_empty_text(client, app, auth_header):
    _register_workspace(app, "run_live")
    r = await client.post(
        "/api/runs/run_live/comment",
        json={"text": "   "},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_post_comment_rejects_invalid_priority(client, app, auth_header):
    _register_workspace(app, "run_live")
    r = await client.post(
        "/api/runs/run_live/comment",
        json={"text": "x", "priority": "URGENT"},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_post_comment_uses_workspace_iteration(client, app, auth_header):
    """The signal's iteration field reflects the workspace's current
    iteration so agents can absorb it within the right cycle."""
    ws = _register_workspace(app, "run_live")
    ws.iteration = 3
    r = await client.post(
        "/api/runs/run_live/comment",
        json={"text": "iter check"},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json()["iteration"] == 3
    assert ws.board[0].iteration == 3


async def test_post_comment_notifies_subscribers(client, app, auth_header):
    """A subscriber on the workspace receives the new USER_COMMENT live —
    proves the WebSocket bridge would forward it without extra wiring."""
    ws = _register_workspace(app, "run_live")
    received: list = []
    ws.subscribe(lambda sig: received.append(sig))
    r = await client.post(
        "/api/runs/run_live/comment",
        json={"text": "live"},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert len(received) == 1
    assert received[0].type.value == "USER_COMMENT"
    assert received[0].summary == "live"


# ── POST /api/runs/{id}/approval/{approval_id} (Phase C.0.5) ───────────────


def _register_workspace_with_approval(app, run_id: str, approval_id: str, kind: str = "test"):
    from core.workspace import ApprovalRequest, Workspace
    ws = Workspace(run_id=run_id, goal="x")
    ws.pending_approvals.append(ApprovalRequest(
        id=approval_id, kind=kind, summary="pls approve",
    ))
    app.state.run_registry.runs[run_id] = ws
    return ws


async def test_post_approval_grant_records_decision_and_signals(client, app, auth_header):
    ws = _register_workspace_with_approval(app, "run_appr", "appr_xyz")
    r = await client.post(
        "/api/runs/run_appr/approval/appr_xyz",
        json={"decision": "grant", "note": "lgtm"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["decision"] == "granted"
    assert body["type"] == "APPROVAL_GRANTED"

    # Pending approval is now decided.
    req = next(a for a in ws.pending_approvals if a.id == "appr_xyz")
    assert req.decision == "granted"
    assert req.decision_note == "lgtm"

    # The matching APPROVAL_GRANTED signal landed on the board.
    types = [s.type.value for s in ws.board]
    assert "APPROVAL_GRANTED" in types
    granted = next(s for s in ws.board if s.type.value == "APPROVAL_GRANTED")
    assert "approval_id=appr_xyz" in (granted.detail or "")


async def test_post_approval_deny(client, app, auth_header):
    ws = _register_workspace_with_approval(app, "run_appr", "appr_xyz")
    r = await client.post(
        "/api/runs/run_appr/approval/appr_xyz",
        json={"decision": "deny"},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "denied"
    types = [s.type.value for s in ws.board]
    assert "APPROVAL_DENIED" in types


async def test_post_approval_404_for_unknown_run(client, auth_header):
    r = await client.post(
        "/api/runs/run_ghost/approval/appr_x",
        json={"decision": "grant"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_post_approval_404_for_unknown_approval_id(client, app, auth_header):
    _register_workspace_with_approval(app, "run_appr", "appr_xyz")
    r = await client.post(
        "/api/runs/run_appr/approval/appr_other",
        json={"decision": "grant"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_post_approval_rejects_invalid_decision(client, app, auth_header):
    _register_workspace_with_approval(app, "run_appr", "appr_xyz")
    r = await client.post(
        "/api/runs/run_appr/approval/appr_xyz",
        json={"decision": "MAYBE"},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_post_approval_requires_auth(client, app):
    _register_workspace_with_approval(app, "run_appr", "appr_xyz")
    r = await client.post(
        "/api/runs/run_appr/approval/appr_xyz",
        json={"decision": "grant"},
    )
    assert r.status_code == 401


async def test_post_approval_idempotent_returns_404_after_decided(client, app, auth_header):
    """Once decided, the same id can't be decided again — keeps the
    semantics simple (each gate fires once)."""
    _register_workspace_with_approval(app, "run_appr", "appr_xyz")
    await client.post(
        "/api/runs/run_appr/approval/appr_xyz",
        json={"decision": "grant"},
        headers=auth_header,
    )
    r = await client.post(
        "/api/runs/run_appr/approval/appr_xyz",
        json={"decision": "deny"},
        headers=auth_header,
    )
    assert r.status_code == 404
