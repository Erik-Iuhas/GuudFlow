"""Worker context plan H over HTTP: prepare, answer, approve, start from the plan, and the run's
plan checkpoint (approve, send back)."""

from __future__ import annotations

import json
from typing import Any

from core.llm.client import MockLLMClient, MockTurn, ToolCall
from core.preflight import TOOL_NAME
from core.signals import SignalType
from core.workspace import HumanRequest, Workspace

TEAM = [
    {"role": "tech_lead", "role_type": "architect", "contract": "tech_lead"},
    {"role": "dev", "role_type": "builder", "depends_on": ["tech_lead"]},
]
CARD = {"id": "T1", "title": "Draw the board", "owner_role": "dev",
        "acceptance": ["the board shows 8x8 squares"], "size": "S", "kind": "code",
        "backlog_id": "B1"}


def _turn(assumptions: list[dict[str, Any]] | None = None) -> MockTurn:
    return MockTurn(tool_calls=[ToolCall("1", TOOL_NAME, {
        "reply": "Here is the plan.", "plan": {"tasks": [CARD], "notes": ""},
        "assumptions": assumptions or []})])


async def _project(client, auth_header) -> None:
    r = await client.post("/api/projects", headers=auth_header, json={
        "project_id": "p1", "goal": "A tiny game", "agents": TEAM,
        "approval_gates": {"pre_run_cost_preview": False}})
    assert r.status_code in (200, 201), r.text
    r = await client.post("/api/projects/p1/backlog", headers=auth_header,
                          json={"title": "Draw the board", "acceptance": ["the board shows"]})
    assert r.status_code in (200, 201), r.text


async def test_prepare_answer_approve_and_start(client, auth_header, app, monkeypatch) -> None:
    monkeypatch.setattr("api.routes.invoke.runtime_check.missing_runtimes", lambda *a: [])
    await _project(client, auth_header)
    app.state.meta_llm = MockLLMClient([_turn([{"text": "How big is the window?",
                                                "decided_by": "owner"}])])
    assert (await client.get("/api/projects/p1/preflight", headers=auth_header)).json() == {
        "plan": None}
    r = await client.post("/api/projects/p1/preflight/prepare", headers=auth_header,
                          json={"items": ["B1"]})
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["id"] == "P1" and plan["open_questions"] == ["A1"]

    r = await client.post("/api/invoke/p1", headers=auth_header,
                          json={"preflight": "P1", "confirm": True})
    assert r.status_code == 409 and "A1" in r.json()["detail"]      # open question blocks Start
    r = await client.post("/api/projects/p1/preflight/approve", headers=auth_header)
    assert r.status_code == 409

    r = await client.post("/api/projects/p1/preflight/assumptions/A1", headers=auth_header,
                          json={"answer": "800 by 600"})
    assert r.status_code == 200 and r.json()["plan"]["open_questions"] == []
    spec = (await client.get("/api/projects/p1/spec", headers=auth_header)).json()
    assert any(d["choice"] == "800 by 600" for d in spec["decisions"])

    r = await client.put("/api/projects/p1/preflight/cards", headers=auth_header,
                         json={"tasks": [{**CARD, "owner_role": "ghost"}]})
    assert r.status_code == 422 and "ghost" in r.text

    r = await client.post("/api/projects/p1/preflight/approve", headers=auth_header)
    assert r.status_code == 200, r.text
    r = await client.post("/api/invoke/p1", headers=auth_header,
                          json={"preflight": "P1", "confirm": True})
    assert r.status_code == 202, r.text
    jobs = await app.state.queue.list(project_id="p1", limit=5)
    assert jobs[0].payload["backlog_items"] == ["B1"]


async def test_a_stale_plan_needs_start_anyway(client, auth_header, app, monkeypatch) -> None:
    monkeypatch.setattr("api.routes.invoke.runtime_check.missing_runtimes", lambda *a: [])
    await _project(client, auth_header)
    app.state.meta_llm = MockLLMClient([_turn()])
    await client.post("/api/projects/p1/preflight/prepare", headers=auth_header,
                      json={"items": ["B1"]})
    assert (await client.post("/api/projects/p1/preflight/approve",
                              headers=auth_header)).status_code == 200
    r = await client.post("/api/projects/p1/spec/edit", headers=auth_header, json={"change": {
        "op": "add_decision", "ref": "new:1", "title": "Storage", "choice": "files",
        "reason": "nothing to install for the owner"}})
    assert r.status_code == 200, r.text
    r = await client.post("/api/invoke/p1", headers=auth_header,
                          json={"preflight": "P1", "confirm": True})
    assert r.status_code == 409
    assert r.json()["detail"]["gate"] == "preflight_stale"
    r = await client.post("/api/invoke/p1", headers=auth_header,
                          json={"preflight": "P1", "confirm": True, "stale_ok": True})
    assert r.status_code == 202, r.text


def _paused(app, run_id: str) -> Workspace:
    ws = Workspace(run_id=run_id, goal="x")
    ws.pending_human = HumanRequest(from_agent="planner", question="Review the plan",
                                    context="plan_review")
    app.state.run_registry.runs[run_id] = ws
    return ws


async def test_plan_checkpoint_approve_and_send_back(client, auth_header, app) -> None:
    ws = _paused(app, "run_a")
    r = await client.post("/api/runs/run_a/plan/approve", headers=auth_header)
    assert r.status_code == 200 and ws.pending_human is None
    assert ws.board[-1].type is SignalType.HUMAN_RESPONDED
    assert (await client.post("/api/runs/run_a/plan/approve",
                              headers=auth_header)).status_code == 409   # nothing to review

    ws = _paused(app, "run_b")
    sent: list[str] = []
    ws.controls["plan_send_back"] = lambda note: sent.append(note) or ["T1"]
    r = await client.post("/api/runs/run_b/plan/send-back", headers=auth_header,
                          json={"note": "Use one module"})
    assert r.status_code == 200 and r.json()["cancelled"] == ["T1"]
    assert sent == ["Use one module"]
    assert json.loads(ws.board[-1].detail or "{}")["plan_review"] == "sent_back"
