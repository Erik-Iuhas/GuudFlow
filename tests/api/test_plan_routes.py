"""Tests for /api/projects/{id}/spec and /plan, and the spec-first invoke gate."""

from __future__ import annotations

from typing import Any

from core.llm.client import MockLLMClient, MockTurn, ToolCall
from core.project_manager import TOOL_NAME


def _call(reply: str, changes: list[dict[str, Any]] | None = None, **extra: Any) -> MockTurn:
    return MockTurn(tool_calls=[ToolCall("1", TOOL_NAME, {
        "reply": reply, "changes": changes or [], **extra})])


REQ = {"op": "add_requirement", "ref": "new:1", "text": "Save a recipe", "kind": "feature",
       "priority": "must", "checks": ["a saved recipe shows in the list"]}
ITEM = {"op": "add_item", "ref": "new:2", "title": "Build the list", "serves": ["new:1"]}
DEC = {"op": "add_decision", "ref": "new:3", "title": "Storage", "choice": "SQLite",
       "reason": "boring and testable, no server to run", "binding": True}


async def _project(client, auth_header, pid: str = "p1", **doc: Any) -> None:
    r = await client.post("/api/projects", json={"project_id": pid, "goal": "A recipe box",
                                                 **doc}, headers=auth_header)
    assert r.status_code in (200, 201)


def _llm(app, *turns: MockTurn) -> MockLLMClient:
    app.state.meta_llm = MockLLMClient(list(turns))
    return app.state.meta_llm


async def test_routes_need_the_token(client, auth_header) -> None:
    await _project(client, auth_header)
    for method, url in [("get", "/spec"), ("get", "/plan"), ("post", "/plan/start"),
                        ("post", "/plan/messages"), ("patch", "/plan/draft/c1"),
                        ("post", "/plan/draft/approve"), ("post", "/plan/draft/discard"),
                        ("post", "/spec/edit")]:
        r = await getattr(client, method)(f"/api/projects/p1{url}")
        assert r.status_code == 401, url


async def test_unknown_project_is_404(client, auth_header) -> None:
    assert (await client.get("/api/projects/ghost/spec", headers=auth_header)).status_code == 404
    assert (await client.get("/api/projects/ghost/plan", headers=auth_header)).status_code == 404


async def test_empty_spec_and_plan(client, auth_header) -> None:
    await _project(client, auth_header)
    spec = (await client.get("/api/projects/p1/spec", headers=auth_header)).json()
    assert spec == {"version": 0, "requirements": [], "decisions": [], "rules": []}
    plan = (await client.get("/api/projects/p1/plan", headers=auth_header)).json()
    assert plan == {"messages": [], "draft": None}


async def test_full_flow_start_message_tick_approve(client, auth_header, app) -> None:
    await _project(client, auth_header)
    _llm(app, _call("Here is a first draft.", [REQ, DEC]),
         _call("Added the list item.", [REQ, DEC, ITEM], questions=["Offline use?"]))

    r = await client.post("/api/projects/p1/plan/start", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["author"] for m in body["messages"]] == ["pm"]
    assert set(body["messages"][0]) >= {"id", "at", "author", "text", "draft_id", "mode",
                                        "cost_usd", "tokens"}
    assert body["draft"]["status"] == "open"
    assert [c["cid"] for c in body["draft"]["changes"]] == ["c1", "c2"]
    assert body["messages"][0]["draft_id"] == body["draft"]["id"]
    # starting twice is refused
    again = await client.post("/api/projects/p1/plan/start", headers=auth_header)
    assert again.status_code == 409

    r = await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                          json={"text": "Add a list screen", "mode": "update"})
    assert r.status_code == 200
    body = r.json()
    assert [m["author"] for m in body["messages"]] == ["human", "pm"]
    assert "Offline use?" in body["messages"][1]["text"]
    ops = [c["change"]["op"] for c in body["draft"]["changes"]]
    assert ops == ["add_requirement", "add_decision", "add_item"]
    # the PM's decision is pre-ticked (lead empty), the others are undecided
    assert [c["accepted"] for c in body["draft"]["changes"]] == [None, True, None]

    plan = (await client.get("/api/projects/p1/plan", headers=auth_header)).json()
    assert len(plan["messages"]) == 3 and plan["draft"]["id"] == body["draft"]["id"]

    # approving with undecided changes is refused
    r = await client.post("/api/projects/p1/plan/draft/approve", headers=auth_header)
    assert r.status_code == 409 and "tick or reject" in r.json()["detail"]

    r = await client.patch("/api/projects/p1/plan/draft/c3", headers=auth_header,
                           json={"accepted": True})
    assert r.status_code == 200 and r.json()["changes"][0]["accepted"] is True
    r = await client.patch("/api/projects/p1/plan/draft/c5", headers=auth_header,
                           json={"accepted": True})
    assert r.status_code == 200
    assert (await client.patch("/api/projects/p1/plan/draft/c9", headers=auth_header,
                               json={"accepted": True})).status_code == 404

    r = await client.post("/api/projects/p1/plan/draft/approve", headers=auth_header)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["version"] == 1 and result["applied"] == ["c3", "c4", "c5"]
    assert set(result["created"]) == {"new:1", "new:2", "new:3"}
    assert result["created"]["new:1"] == "R1" and result["created"]["new:2"] == "B1"

    spec = (await client.get("/api/projects/p1/spec", headers=auth_header)).json()
    assert spec["version"] == 1
    assert spec["requirements"][0]["id"] == "R1"
    assert spec["requirements"][0]["checks"][0]["id"] == "R1.1"
    assert spec["decisions"][0]["id"] == "D1" and spec["decisions"][0]["binding"] is True
    assert [r_["source"] for r_ in spec["rules"]] == ["D1"]
    backlog = (await client.get("/api/projects/p1/backlog", headers=auth_header)).json()["items"]
    assert backlog[0]["serves"] == ["R1"]
    assert any(a.startswith("R1.1:") for a in backlog[0]["acceptance"])
    assert (await client.get("/api/projects/p1/plan", headers=auth_header)).json()["draft"] is None
    # nothing left to approve
    assert (await client.post("/api/projects/p1/plan/draft/approve",
                              headers=auth_header)).status_code == 409


async def test_discuss_does_not_touch_the_draft(client, auth_header, app) -> None:
    await _project(client, auth_header)
    _llm(app, _call("Talking only.", [REQ]))
    r = await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                          json={"text": "thoughts?", "mode": "discuss"})
    assert r.status_code == 200 and r.json()["draft"] is None
    assert r.json()["messages"][1]["mode"] == "discuss"


async def test_message_validation(client, auth_header, app) -> None:
    await _project(client, auth_header)
    r = await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                          json={"text": "  ", "mode": "update"})
    assert r.status_code == 422
    r = await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                          json={"text": "hi", "mode": "shout"})
    assert r.status_code == 422


async def test_discard(client, auth_header, app) -> None:
    await _project(client, auth_header)
    _llm(app, _call("draft", [REQ]))
    await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                      json={"text": "go", "mode": "update"})
    r = await client.post("/api/projects/p1/plan/draft/discard", headers=auth_header)
    assert r.status_code == 200 and r.json() == {}
    assert (await client.get("/api/projects/p1/plan", headers=auth_header)).json()["draft"] is None
    assert (await client.get("/api/projects/p1/spec", headers=auth_header)).json()["version"] == 0
    assert (await client.post("/api/projects/p1/plan/draft/discard",
                              headers=auth_header)).status_code == 409
    assert (await client.patch("/api/projects/p1/plan/draft/c1", headers=auth_header,
                               json={"accepted": True})).status_code == 409


async def test_llm_failure_is_502(client, auth_header, app) -> None:
    await _project(client, auth_header)

    class Exploding:
        async def complete(self, *a: Any, **k: Any):
            raise RuntimeError("provider down")

    app.state.meta_llm = Exploding()
    r = await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                          json={"text": "go", "mode": "update"})
    assert r.status_code == 502 and "provider down" in r.json()["detail"]


async def test_invalid_pm_output_is_502_with_problems(client, auth_header, app) -> None:
    await _project(client, auth_header)
    bad = _call("bad", [{"op": "edit_requirement", "id": "R9", "text": "x"}])
    _llm(app, bad, bad)
    r = await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                          json={"text": "go", "mode": "update"})
    assert r.status_code == 502 and "R9" in r.json()["detail"]


async def test_direct_edit(client, auth_header, app) -> None:
    await _project(client, auth_header)
    r = await client.post("/api/projects/p1/spec/edit", headers=auth_header,
                          json={"change": REQ})
    assert r.status_code == 200, r.text
    assert r.json() == {"version": 1, "applied": ["c1"], "created": {"new:1": "R1"}}
    r = await client.post("/api/projects/p1/spec/edit", headers=auth_header, json={
        "change": {"op": "edit_requirement", "id": "R1", "text": "Save a recipe with photos"}})
    assert r.status_code == 200 and r.json()["version"] == 2
    spec = (await client.get("/api/projects/p1/spec", headers=auth_header)).json()
    assert spec["requirements"][0]["text"] == "Save a recipe with photos"
    assert spec["requirements"][0]["version"] == 2


async def test_direct_edit_errors(client, auth_header) -> None:
    await _project(client, auth_header)
    r = await client.post("/api/projects/p1/spec/edit", headers=auth_header,
                          json={"change": {"op": "teleport"}})
    assert r.status_code == 422 and r.json()["detail"]
    r = await client.post("/api/projects/p1/spec/edit", headers=auth_header, json={
        "change": {"op": "edit_requirement", "id": "R7", "text": "nothing here"}})
    assert r.status_code == 400 and "R7" in r.json()["detail"]
    assert (await client.get("/api/projects/p1/spec", headers=auth_header)).json()["version"] == 0


async def test_lead_held_decisions_start_unticked(client, auth_header, app) -> None:
    await _project(client, auth_header, seats={"lead": True, "reviewer": "off"})
    _llm(app, _call("Options.", [DEC]))
    r = await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                          json={"text": "storage?", "mode": "update"})
    assert r.json()["draft"]["changes"][0]["accepted"] is None


# ── spec-first gate on invoke ───────────────────────────────────────────────
async def test_spec_first_project_is_gated_until_the_plan_is_approved(
        client, auth_header) -> None:
    await _project(client, auth_header, start_mode="spec_first")
    r = await client.post("/api/invoke/p1", headers=auth_header, json={"confirm": True})
    assert r.status_code == 409 and r.json()["detail"] == "approve the plan first"
    forced = await client.post("/api/invoke/p1", headers=auth_header,
                               json={"confirm": True, "force": True})
    assert forced.status_code == 202
    await client.post("/api/projects/p1/spec/edit", headers=auth_header, json={"change": REQ})
    r = await client.post("/api/invoke/p1", headers=auth_header,
                          json={"confirm": True, "allow_parallel": True})
    assert r.status_code == 202


async def test_test_first_is_gated_too(client, auth_header) -> None:
    await _project(client, auth_header, start_mode="test_first")
    r = await client.post("/api/invoke/p1", headers=auth_header, json={"confirm": True})
    assert r.status_code == 409


async def test_other_modes_and_unset_mode_are_not_gated(client, auth_header) -> None:
    await _project(client, auth_header, "p2", start_mode="prototype_first")
    await _project(client, auth_header, "p3")
    for pid in ("p2", "p3"):
        r = await client.post(f"/api/invoke/{pid}", headers=auth_header, json={"confirm": True})
        assert r.status_code == 202, pid
