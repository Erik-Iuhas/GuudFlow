"""Tests for /api/meta."""

from __future__ import annotations


async def test_propose_returns_skeleton(client, auth_header):
    r = await client.post(
        "/api/meta/propose",
        json={"goal": "Build a JWT auth flow"},
        headers=auth_header,
    )
    assert r.status_code == 200
    body = r.json()
    assert "rationale" in body
    assert len(body["agents"]) >= 1
    # Includes a developer + reviewer baseline.
    roles = {a["role"] for a in body["agents"]}
    assert "developer" in roles
    assert "reviewer" in roles
    assert "rbac" in body


async def test_propose_requires_goal(client, auth_header):
    r = await client.post("/api/meta/propose", json={}, headers=auth_header)
    assert r.status_code == 400


async def test_propose_requires_auth(client):
    r = await client.post("/api/meta/propose", json={"goal": "x"})
    assert r.status_code == 401
