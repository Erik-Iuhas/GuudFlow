"""Tests for /api/contracts and /api/projects/{id}/contracts."""

from __future__ import annotations


async def test_library_and_project_team(client, auth_header, data_root) -> None:
    r = await client.get("/api/contracts", headers=auth_header)
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()["contracts"]}
    assert {"tech_lead", "builder", "product_manager"} <= ids
    assert (await client.get("/api/contracts")).status_code == 401
    r = await client.post("/api/projects", headers=auth_header, json={
        "project_id": "p1", "goal": "g", "agents": [
            {"role": "lead", "role_type": "architect", "contract": "tech_lead"},
            {"role": "sol", "role_type": "builder"},
            {"role": "rev", "is_reviewer": True}]})
    assert r.status_code in (200, 201)
    over = data_root / "projects" / "p1" / "contracts"
    over.mkdir(parents=True, exist_ok=True)
    (over / "builder.yaml").write_text("rules:\n  - Use integer maths.\n")
    body = (await client.get("/api/projects/p1/contracts", headers=auth_header)).json()
    assert {t["role"]: t["contract"] for t in body["team"]} == {
        "lead": "tech_lead", "sol": "builder", "rev": "reviewer"}
    builder = next(c for c in body["contracts"] if c["id"] == "builder")
    assert builder["source"] == "project" and builder["rules"][-1] == "Use integer maths."
    assert (await client.get("/api/projects/nope/contracts", headers=auth_header)).status_code == 404
