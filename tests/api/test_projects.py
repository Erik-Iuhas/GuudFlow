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
