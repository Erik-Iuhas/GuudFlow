"""Tests for POST /api/oneshot (in-memory queue, no worker running)."""

from __future__ import annotations

import pytest


async def test_demo_enqueues_and_returns_202(client, auth_header, app):
    r = await client.post("/api/oneshot", json={"goal": "Build a thing", "demo": True, "budget": 3,
                                                "web": "off"}, headers=auth_header)
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True and body["needs_approval"] is False
    assert body["job_id"].startswith("job_") and body["run_id"].startswith("run_")
    assert body["project_id"].startswith("oneshot-")
    assert body["status_url"] == f"/api/invoke/{body['job_id']}/status"

    job = await app.state.queue.get(body["job_id"])
    assert job is not None and job.project_id == body["project_id"] and job.status == "queued"

    stored = await app.state.project_store.get(body["project_id"])
    assert stored["harness"] == "opencode" and stored["goal"] == "Build a thing"
    assert stored["approval_gates"]["token_budget_usd"] == 3
    assert stored["approval_gates"]["demo"] is True
    assert "researcher" not in [a["role"] for a in stored["agents"]]

    status = await client.get(body["status_url"], headers=auth_header)
    assert status.status_code == 200 and status.json()["status"] == "queued"


async def test_default_includes_researcher_and_defaults(client, auth_header, app):
    r = await client.post("/api/oneshot", json={"goal": "g", "confirm": True}, headers=auth_header)
    assert r.status_code == 202 and r.json()["accepted"] is True
    stored = await app.state.project_store.get(r.json()["project_id"])
    assert [a["role"] for a in stored["agents"]][0] == "researcher"
    assert stored["approval_gates"]["token_budget_usd"] == 5.0
    assert stored["approval_gates"]["demo"] is False


async def test_without_demo_or_confirm_needs_approval_and_does_not_enqueue(client, auth_header, app):
    r = await client.post("/api/oneshot", json={"goal": "g"}, headers=auth_header)
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is False and body["needs_approval"] is True
    assert "job_id" not in body
    assert body["confirm_url"] == f"/api/invoke/{body['project_id']}"
    assert body["estimated_cost"]["total_usd"] >= 0
    assert await app.state.queue.list() == []
    assert await app.state.project_store.get(body["project_id"]) is not None

    # The documented follow-up: confirm through the invoke path.
    r2 = await client.post(body["confirm_url"], json={"confirm": True}, headers=auth_header)
    assert r2.status_code == 202 and r2.json()["accepted"] is True
    assert len(await app.state.queue.list()) == 1


async def test_repo_is_stored_not_cloned_and_not_enqueued(client, auth_header, app):
    r = await client.post("/api/oneshot", json={"goal": "g", "demo": True,
                                                "repo": "https://example.com/x.git"},
                          headers=auth_header)
    assert r.status_code == 202
    body = r.json()
    assert body["needs_approval"] is True and body["accepted"] is False
    assert body["repo_import"]["url"] == f"/api/projects/{body['project_id']}/inputs/git"
    assert body["repo_import"]["body"] == {"git_url": "https://example.com/x.git"}
    assert await app.state.queue.list() == []


@pytest.mark.parametrize("payload", [
    {}, {"goal": ""}, {"goal": "  "}, {"goal": "x", "budget": 0}, {"goal": "x", "budget": -2},
    {"goal": "x", "budget": 5000}, {"goal": "x", "web": "everywhere"}, {"goal": "x", "demo": "maybe"},
    {"goal": "x", "surprise": 1}, {"goal": 7},
])
async def test_bad_input_is_400(client, auth_header, app, payload):
    r = await client.post("/api/oneshot", json=payload, headers=auth_header)
    assert r.status_code == 400, r.text
    assert await app.state.queue.list() == []
    assert await app.state.project_store.list() == []


async def test_requires_auth(client, app):
    r = await client.post("/api/oneshot", json={"goal": "g", "demo": True})
    assert r.status_code == 401
    r = await client.post("/api/oneshot", json={"goal": "g", "demo": True},
                          headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert await app.state.queue.list() == []
