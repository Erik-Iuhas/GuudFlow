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


@pytest.mark.asyncio
async def test_diff_with_base_covers_the_whole_merged_range(app, data_root):
    """A card's merge is (base, head): the drawer must see every commit in it."""
    from git import Repo

    work = data_root / "runs" / "r1" / "workspace" / "working"
    work.mkdir(parents=True)
    repo = Repo.init(work)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "t").set_value("user", "email", "t@x")
    (work / "a.py").write_text("a = 1\n")
    repo.index.add(["a.py"])
    base = repo.index.commit("base").hexsha
    (work / "b.py").write_text("b = 1\n")
    repo.index.add(["b.py"])
    repo.index.commit("one")
    (work / "c.py").write_text("c = 1\n")
    repo.index.add(["c.py"])
    head = repo.index.commit("two").hexsha

    transport = httpx.ASGITransport(app=app)
    auth = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 headers=auth) as client:
        single = (await client.get(f"/api/runs/r1/git/diff?ref={head}")).json()["diff"]
        ranged = (await client.get(f"/api/runs/r1/git/diff?ref={head}&base={base}")).json()["diff"]
        missing = await client.get(f"/api/runs/r1/git/diff?ref={head}&base=deadbeef")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
        assert (await anon.get(f"/api/runs/r1/git/diff?ref={head}")).status_code == 401
    assert "c.py" in single and "b.py" not in single
    assert "b.py" in ranged and "c.py" in ranged
    assert missing.status_code == 404
