"""Smoke-check that all git routes are registered on the FastAPI app.

Guards against the branch-picker 404 caused by a stale running server
not picking up new routes after source changes.
"""
from __future__ import annotations

import pytest
import httpx


_EXPECTED_PATHS = [
    "/api/runs/{run_id}/git/commits",
    "/api/runs/{run_id}/git/diff",
    "/api/runs/{run_id}/git/branches",
    "/api/runs/{run_id}/git/tree",
]


@pytest.mark.asyncio
async def test_all_git_routes_registered(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi.json")
        openapi = resp.json()

    registered = set(openapi.get("paths", {}).keys())
    for path in _EXPECTED_PATHS:
        assert path in registered, (
            f"Route {path!r} not registered. "
            f"Known paths: {sorted(p for p in registered if '/git/' in p)}"
        )
