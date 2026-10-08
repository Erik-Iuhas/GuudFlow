"""Tests for /api/projects/{id}/design (role contracts plan §4.2)."""

from __future__ import annotations

from core.design import DesignDrafts, DesignTurn

WHY = {"name": "Recover", "summary": "The design as the code has it.",
       "because": "Nothing was written down yet."}
LOGIC = {"op": "add_component", "ref": "new:1", "name": "Game logic", "group": "Recover",
         "paths": ["web/src/logic/**"], "responsibility": "Rules of play, pure and deterministic."}


async def test_read_edit_and_decide_design(client, auth_header, data_root) -> None:
    r = await client.post("/api/projects", headers=auth_header,
                           json={"project_id": "p1", "goal": "g"})
    assert r.status_code in (200, 201)
    body = (await client.get("/api/projects/p1/design", headers=auth_header)).json()
    assert body["design"]["empty"] is True and body["drafts"] == [] and body["markdown"] == ""
    r = await client.post("/api/projects/p1/design/edit", headers=auth_header,
                          json={"turn": {"groups": [WHY], "changes": [LOGIC]}})
    assert r.status_code == 200 and r.json()["created"] == {"new:1": "C1"}
    body = (await client.get("/api/projects/p1/design", headers=auth_header)).json()
    assert body["design"]["components"][0]["name"] == "Game logic"
    assert "### C1 Game logic" in body["markdown"]
    # A tech lead's draft waits for the Lead.
    out = DesignDrafts(data_root, "p1").propose(DesignTurn.model_validate({
        "groups": [WHY], "changes": [{**LOGIC, "ref": "new:1", "name": "Render",
                                      "paths": ["web/src/render/**"]}]}),
        by="tech_lead", auto_apply=False)
    drafts = (await client.get("/api/projects/p1/design", headers=auth_header)).json()["drafts"]
    assert drafts[0]["id"] == out.draft_id and drafts[0]["status"] == "open"
    r = await client.post(f"/api/projects/p1/design/drafts/{out.draft_id}/approve",
                          headers=auth_header, json={})
    assert r.status_code == 200 and r.json()["created"] == {"new:1": "C2"}
    again = await client.post(f"/api/projects/p1/design/drafts/{out.draft_id}/discard",
                              headers=auth_header)
    assert again.status_code == 400
    bad = await client.post("/api/projects/p1/design/edit", headers=auth_header,
                            json={"turn": {"groups": [WHY], "changes": [
                                {**LOGIC, "ref": "new:1", "name": "Again"}]}})
    assert bad.status_code == 400 and "already owned by C1" in bad.json()["detail"]
    assert (await client.get("/api/projects/nope/design", headers=auth_header)).status_code == 404


async def test_knowledge_routes(client, auth_header, data_root) -> None:
    from core.knowledge import KnowledgeNote, add_notes
    await client.post("/api/projects", headers=auth_header, json={"project_id": "p1", "goal": "g"})
    add_notes(data_root, "p1", [KnowledgeNote(text="Run bun test from the project root.",
                                              topic="Commands")], by="lead", approve=False)
    body = (await client.get("/api/projects/p1/knowledge", headers=auth_header)).json()
    assert body["facts"] is None and body["notes"][0]["status"] == "proposed"
    r = await client.post("/api/projects/p1/knowledge/K1/approve", headers=auth_header)
    assert r.json()["status"] == "approved" and r.json()["approved_by"] == "human"
    assert (await client.post("/api/projects/p1/knowledge/K9/drop",
                              headers=auth_header)).status_code == 404
    assert (await client.post("/api/projects/p1/knowledge/rebuild",
                              headers=auth_header)).status_code == 409
