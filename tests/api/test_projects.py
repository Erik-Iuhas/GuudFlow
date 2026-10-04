"""Tests for /api/projects."""

from __future__ import annotations

import pytest


# ── Health check ────────────────────────────────────────────────────────────


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── List + read ─────────────────────────────────────────────────────────────


async def test_list_projects_empty(client):
    r = await client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_and_list_project(client, auth_header):
    r = await client.post("/api/projects", json={
        "project_id": "p1", "goal": "build x", "agents": [],
    }, headers=auth_header)
    assert r.status_code == 201

    r = await client.get("/api/projects")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["project_id"] == "p1"


async def test_get_project(client, auth_header):
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "x", "agents": [],
    }, headers=auth_header)
    r = await client.get("/api/projects/p1")
    assert r.status_code == 200
    assert r.json()["project_id"] == "p1"


async def test_get_unknown_project(client):
    r = await client.get("/api/projects/ghost")
    assert r.status_code == 404


# ── Create validation ──────────────────────────────────────────────────────


async def test_create_requires_project_id(client, auth_header):
    r = await client.post("/api/projects", json={"goal": "x"}, headers=auth_header)
    assert r.status_code == 400


async def test_create_requires_goal(client, auth_header):
    r = await client.post("/api/projects", json={"project_id": "p1"}, headers=auth_header)
    assert r.status_code == 400


async def test_create_duplicate(client, auth_header):
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "x",
    }, headers=auth_header)
    r = await client.post("/api/projects", json={
        "project_id": "p1", "goal": "y",
    }, headers=auth_header)
    assert r.status_code == 409


# ── Update + delete ────────────────────────────────────────────────────────


async def test_update_project(client, auth_header):
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "old",
    }, headers=auth_header)
    r = await client.put("/api/projects/p1", json={
        "project_id": "p1", "goal": "new",
    }, headers=auth_header)
    assert r.status_code == 200
    assert r.json()["goal"] == "new"


async def test_update_unknown(client, auth_header):
    r = await client.put("/api/projects/ghost", json={
        "project_id": "ghost", "goal": "x",
    }, headers=auth_header)
    assert r.status_code == 404


async def test_delete_project(client, auth_header):
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "x",
    }, headers=auth_header)
    r = await client.delete("/api/projects/p1", headers=auth_header)
    assert r.status_code == 204
    r = await client.get("/api/projects/p1")
    assert r.status_code == 404


# ── Auth ───────────────────────────────────────────────────────────────────


async def test_create_requires_auth(client):
    r = await client.post("/api/projects", json={
        "project_id": "p1", "goal": "x",
    })
    assert r.status_code == 401


async def test_create_rejects_wrong_token(client):
    r = await client.post("/api/projects", json={
        "project_id": "p1", "goal": "x",
    }, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_list_does_not_require_auth(client):
    """Read-only routes are open in dev mode."""
    r = await client.get("/api/projects")
    assert r.status_code == 200


# ── Phase K.0 — project roadmap document ──────────────────────────────────


async def _create_project_for_roadmap(client, auth_header, project_id: str = "p1"):
    r = await client.post(
        "/api/projects",
        json={"project_id": project_id, "goal": "x"},
        headers=auth_header,
    )
    assert r.status_code == 201, r.text


async def test_get_roadmap_for_unknown_project_404(client):
    r = await client.get("/api/projects/ghost/roadmap")
    assert r.status_code == 404


async def test_get_roadmap_when_never_created(client, auth_header):
    """A project without a roadmap.md is the default state. Endpoint
    returns content='' + exists=False so the studio can show the
    empty-state copy."""
    await _create_project_for_roadmap(client, auth_header)
    r = await client.get("/api/projects/p1/roadmap")
    assert r.status_code == 200
    assert r.json() == {"content": "", "exists": False}


async def test_put_roadmap_writes_and_get_reads_back(client, auth_header):
    await _create_project_for_roadmap(client, auth_header)
    body = "## Goal\nBuild the thing.\n"

    r = await client.put(
        "/api/projects/p1/roadmap",
        json={"content": body},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json() == {"content": body, "exists": True}

    r2 = await client.get("/api/projects/p1/roadmap")
    assert r2.status_code == 200
    assert r2.json() == {"content": body, "exists": True}


async def test_put_empty_roadmap_removes_the_file(
    client, auth_header, data_root,
):
    """Erik's nuance: 'just because a roadmap exists doesn't mean it
    needs to be a roadmap project.' Setting empty content removes
    the file so the layer is skipped at runtime — same observable
    state as 'never had one.'"""
    await _create_project_for_roadmap(client, auth_header)

    await client.put(
        "/api/projects/p1/roadmap",
        json={"content": "## Goal\nx\n"},
        headers=auth_header,
    )
    roadmap_path = data_root / "projects" / "p1" / "context" / "roadmap.md"
    assert roadmap_path.is_file()

    r = await client.put(
        "/api/projects/p1/roadmap",
        json={"content": ""},
        headers=auth_header,
    )
    assert r.status_code == 200
    assert r.json() == {"content": "", "exists": False}
    assert not roadmap_path.exists()


async def test_put_roadmap_for_unknown_project_404(client, auth_header):
    r = await client.put(
        "/api/projects/ghost/roadmap",
        json={"content": "x"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_put_roadmap_requires_auth(client, auth_header):
    await _create_project_for_roadmap(client, auth_header)
    r = await client.put(
        "/api/projects/p1/roadmap",
        json={"content": "x"},
    )
    assert r.status_code == 401


async def test_put_roadmap_rejects_non_string_content(client, auth_header):
    await _create_project_for_roadmap(client, auth_header)
    r = await client.put(
        "/api/projects/p1/roadmap",
        json={"content": ["a", "list"]},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_get_playbook_entries_and_metrics(client, auth_header, data_root):
    from core.playbook import Playbook

    await client.post("/api/projects", json={"project_id": "p_book", "goal": "g"},
                      headers=auth_header)
    pb = Playbook.load(data_root, "p_book")
    entry = pb.add("review_rounds", "builder>reviewer", 3, "run_1", confidence=0.7)
    pb.record_run_metrics("run_1", {"cost_usd": 1.5})
    pb.save()

    r = await client.get("/api/projects/p_book/playbook", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["id"] for e in body["entries"]] == [entry.id]
    first = body["entries"][0]
    assert first["section"] == "review_rounds" and first["value"] == 3
    assert first["evidence_runs"] == ["run_1"] and first["state"] in {"emerging", "active"}
    assert body["metrics"][0]["run_id"] == "run_1"


async def test_get_playbook_empty_404_and_auth(client, auth_header):
    await client.post("/api/projects", json={"project_id": "p_none", "goal": "g"},
                      headers=auth_header)
    r = await client.get("/api/projects/p_none/playbook", headers=auth_header)
    assert r.status_code == 200 and r.json() == {"entries": [], "metrics": []}
    assert (await client.get("/api/projects/missing/playbook", headers=auth_header)).status_code == 404
    assert (await client.get("/api/projects/p_none/playbook")).status_code in (401, 403)
