"""Tests for /api/projects/{id}/record (trace, export, spec-files)."""

from __future__ import annotations

import json
from pathlib import Path

from core.plan_drafts import PlanDrafts, PlanTurn
from tests.spec_fixtures import with_reasons


async def _project(client, auth_header, data_root: Path) -> None:
    r = await client.post("/api/projects", json={"project_id": "p1", "goal": "x"},
                          headers=auth_header)
    assert r.status_code in (200, 201)
    pd = PlanDrafts(data_root, "p1")
    turn = PlanTurn.model_validate(with_reasons({"reply": "ok", "changes": [
        {"op": "add_requirement", "ref": "new:1", "text": "Users can sign in", "kind": "feature",
         "priority": "must", "checks": ["login works"]},
        {"op": "add_decision", "ref": "new:2", "title": "Storage", "choice": "SQLite",
         "reason": "boring and testable, no server to run", "serves": ["new:1"]}]}))
    d = pd.merge_pm_turn(pd.open_draft(), turn, False)
    for c in d.changes:
        pd.set_change(d.id, c.cid, True)
    pd.approve(d.id, "erik")


async def test_routes_need_the_token(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header, data_root)
    for url in ("/record?ref=R1", "/record/export", "/record/spec-files"):
        assert (await client.get(f"/api/projects/p1{url}")).status_code == 401


async def test_unknown_project_and_ref(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header, data_root)
    assert (await client.get("/api/projects/ghost/record?ref=R1", headers=auth_header)).status_code == 404
    assert (await client.get("/api/projects/p1/record?ref=R9", headers=auth_header)).status_code == 404
    assert (await client.get("/api/projects/p1/record", headers=auth_header)).status_code == 422


async def test_trace(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header, data_root)
    body = (await client.get("/api/projects/p1/record?ref=R1", headers=auth_header)).json()
    assert body["ref"] == "R1"
    kinds = [e["kind"] for e in body["events"]]
    assert kinds[0] == "spec" and kinds[-1] == "status"
    assert set(body["events"][0]) == {"at", "kind", "ref", "text", "by", "links"}
    assert "D1" in next(e for e in body["events"] if e["kind"] == "decision")["links"]


async def test_export_download(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header, data_root)
    r = await client.get("/api/projects/p1/record/export?format=md&include=spec,decisions",
                         headers=auth_header)
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="p1-record.md"'
    assert "### R1. Users can sign in" in r.text and "### D1. Storage" in r.text
    j = await client.get("/api/projects/p1/record/export?format=json&include=spec",
                         headers=auth_header)
    assert j.headers["content-disposition"].endswith('p1-record.json"')
    assert json.loads(j.text)["requirements"][0]["id"] == "R1" and "decisions" not in json.loads(j.text)
    assert (await client.get("/api/projects/p1/record/export?include=bogus",
                             headers=auth_header)).status_code == 400
    assert (await client.get("/api/projects/p1/record/export?format=pdf",
                             headers=auth_header)).status_code == 422


async def test_spec_files_preview(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header, data_root)
    files = (await client.get("/api/projects/p1/record/spec-files",
                              headers=auth_header)).json()["files"]
    assert set(files) == {"SPEC.md", "docs/decisions/0001-storage.md"}
    assert "- [ ] R1.1 login works" in files["SPEC.md"]
