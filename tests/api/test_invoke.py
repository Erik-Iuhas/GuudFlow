"""Tests for /api/invoke."""

from __future__ import annotations


async def test_invoke_unknown_project_404(client, auth_header):
    r = await client.post("/api/invoke/ghost", json={}, headers=auth_header)
    assert r.status_code == 404


async def test_invoke_returns_202_with_run_id(client, auth_header):
    """DESIGN §10: invoke returns 202 immediately with run_id."""
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "x",
    }, headers=auth_header)
    r = await client.post("/api/invoke/p1", json={"context": "test"}, headers=auth_header)
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True
    assert body["run_id"].startswith("run_")
    assert body["job_id"].startswith("job_")
    assert body["project_id"] == "p1"


async def test_invoke_requires_auth(client):
    r = await client.post("/api/invoke/p1", json={})
    assert r.status_code == 401


async def test_invoke_enqueues_job(client, auth_header, app):
    await client.post("/api/projects", json={"project_id": "p1", "goal": "x"},
                      headers=auth_header)
    r = await client.post("/api/invoke/p1", json={}, headers=auth_header)
    body = r.json()

    job = await app.state.queue.get(body["job_id"])
    assert job is not None
    assert job.status == "queued"
    assert job.project_id == "p1"


async def test_job_status_endpoint(client, auth_header):
    await client.post("/api/projects", json={"project_id": "p1", "goal": "x"},
                      headers=auth_header)
    r = await client.post("/api/invoke/p1", json={}, headers=auth_header)
    job_id = r.json()["job_id"]

    r = await client.get(f"/api/invoke/{job_id}/status", headers=auth_header)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


async def test_job_status_unknown(client, auth_header):
    r = await client.get("/api/invoke/job_ghost/status", headers=auth_header)
    assert r.status_code == 404


async def test_mcp_manifest_lists_projects(client, auth_header):
    await client.post("/api/projects", json={
        "project_id": "p1", "goal": "build a thing",
    }, headers=auth_header)
    r = await client.get("/api/invoke/mcp/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 1
    assert len(body["tools"]) == 1
    tool = body["tools"][0]
    assert tool["name"] == "agentflow:p1"
    assert "build a thing" in tool["description"]
    assert tool["input_schema"]["type"] == "object"
