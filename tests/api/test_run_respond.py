"""Tests for POST /api/runs/{id}/respond and PATCH /api/runs/{id}/limits."""

from __future__ import annotations

import json
import time

from core.signals import Signal, SignalType
from core.workspace import HumanRequest, Workspace


def _register_workspace(app, run_id: str) -> Workspace:
    ws = Workspace(run_id=run_id, goal="x")
    app.state.run_registry.runs[run_id] = ws
    return ws


def _pause(ws: Workspace, from_agent: str = "overseer", detail: dict | None = None) -> None:
    ws.pending_human = HumanRequest(from_agent=from_agent, question="q?", iteration=0)
    ws.post_signal(Signal(type=SignalType.HUMAN_NEEDED, summary="q?", from_agent=from_agent,
                          detail=json.dumps(detail) if detail else None))


async def test_respond_clears_pause_and_posts_signals(client, app, auth_header):
    ws = _register_workspace(app, "run_resp")
    woken: list[int] = []
    ws.controls["wake"] = lambda: woken.append(1)
    _pause(ws, "dev", {"question_id": "qid1", "question": "q?"})
    r = await client.post("/api/runs/run_resp/respond", json={"answer": "use sqlite"},
                          headers=auth_header)
    assert r.status_code == 200, r.text
    assert r.json()["question_id"] == "qid1"
    assert ws.pending_human is None
    assert woken == [1]
    by_type = {s.type.value: s for s in ws.board}
    responded = by_type["HUMAN_RESPONDED"]
    assert responded.from_user and responded.summary == "use sqlite"
    assert json.loads(responded.detail) == {"answer": "use sqlite", "question_id": "qid1"}
    assert by_type["USER_COMMENT_PRIORITY"].summary == "use sqlite"


async def test_respond_without_question_id(client, app, auth_header):
    ws = _register_workspace(app, "run_resp2")
    _pause(ws)
    r = await client.post("/api/runs/run_resp2/respond", json={"answer": "go"}, headers=auth_header)
    assert r.status_code == 200
    assert r.json()["question_id"] is None
    assert ws.pending_human is None


async def test_respond_409_410_and_validation(client, app, auth_header):
    r = await client.post("/api/runs/ghost/respond", json={"answer": "x"}, headers=auth_header)
    assert r.status_code == 410
    ws = _register_workspace(app, "run_resp3")
    r = await client.post("/api/runs/run_resp3/respond", json={"answer": "x"}, headers=auth_header)
    assert r.status_code == 409
    _pause(ws)
    r = await client.post("/api/runs/run_resp3/respond", json={"answer": ""}, headers=auth_header)
    assert r.status_code == 422
    r = await client.post("/api/runs/run_resp3/respond", json={"answer": "x" * 4001},
                          headers=auth_header)
    assert r.status_code == 422
    assert ws.pending_human is not None


async def test_respond_refuses_a_stopped_run(client, app, auth_header):
    _register_workspace(app, "run_resp4")
    await client.post("/api/runs/run_resp4/stop", headers=auth_header)
    r = await client.post("/api/runs/run_resp4/respond", json={"answer": "x"}, headers=auth_header)
    assert r.status_code == 409


async def test_respond_requires_auth(client, app):
    ws = _register_workspace(app, "run_resp5")
    _pause(ws)
    r = await client.post("/api/runs/run_resp5/respond", json={"answer": "x"})
    assert r.status_code in (401, 403)


async def test_limits_raise_budget_and_extend_deadline(client, app, auth_header):
    ws = _register_workspace(app, "run_lim")
    set_calls: list[float] = []
    ws.controls["set_budget"] = set_calls.append
    ws.controls["spend"] = lambda: 2.0
    ws.deadline_at = time.time() + 600
    before = ws.deadline_at
    r = await client.patch("/api/runs/run_lim/limits",
                           json={"budget_usd": 10, "extend_minutes": 30}, headers=auth_header)
    assert r.status_code == 200, r.text
    assert set_calls == [10]
    assert ws.deadline_at == before + 1800
    body = r.json()
    assert body["budget_usd"] == 10 and body["spend_usd"] == 2.0
    assert any(s.type is SignalType.RUN_LIMITS_CHANGED for s in ws.board)


async def test_limits_validation(client, app, auth_header):
    ws = _register_workspace(app, "run_lim2")
    ws.controls["set_budget"] = lambda usd: None
    ws.controls["spend"] = lambda: 3.0
    url = "/api/runs/run_lim2/limits"
    assert (await client.patch(url, json={}, headers=auth_header)).status_code == 400
    assert (await client.patch(url, json={"budget_usd": 3.0}, headers=auth_header)).status_code == 400
    assert (await client.patch(url, json={"budget_usd": 1001}, headers=auth_header)).status_code == 400
    assert (await client.patch(url, json={"extend_minutes": 0}, headers=auth_header)).status_code == 400
    assert (await client.patch(url, json={"extend_minutes": 241}, headers=auth_header)).status_code == 400
    assert (await client.patch("/api/runs/ghost/limits", json={"extend_minutes": 5},
                               headers=auth_header)).status_code == 410
    assert ws.deadline_at is None


async def test_limits_extend_without_deadline_starts_from_now(client, app, auth_header):
    ws = _register_workspace(app, "run_lim3")
    r = await client.patch("/api/runs/run_lim3/limits", json={"extend_minutes": 15},
                           headers=auth_header)
    assert r.status_code == 200
    assert ws.deadline_at is not None and ws.deadline_at > time.time() + 800


async def test_limits_switch_time_limit_mode(client, app, auth_header):
    ws = _register_workspace(app, "run_lim4")
    ws.deadline_at, ws.deadline_mode, ws.draining = time.time() - 5, "soft", True
    url = "/api/runs/run_lim4/limits"
    r = await client.patch(url, json={"time_limit": "hard"}, headers=auth_header)
    assert r.status_code == 200 and r.json()["time_limit"] == "hard" and ws.deadline_mode == "hard"
    # A later deadline ends a soft drain: new work may start again.
    ws.deadline_mode = "soft"
    r = await client.patch(url, json={"extend_minutes": 15}, headers=auth_header)
    assert r.status_code == 200 and ws.draining is False
    r = await client.patch(url, json={"time_limit": "none"}, headers=auth_header)
    assert r.json()["time_limit"] == "none" and ws.deadline_at is None
    # From "none", a soft or hard limit needs a length.
    r = await client.patch(url, json={"time_limit": "soft"}, headers=auth_header)
    assert r.status_code == 400 and "extend_minutes" in r.json()["detail"]
    r = await client.patch(url, json={"time_limit": "soft", "extend_minutes": 20}, headers=auth_header)
    assert r.status_code == 200 and ws.deadline_at > time.time() + 1100
    assert any(s.type is SignalType.RUN_LIMITS_CHANGED and "time_limit" in s.summary for s in ws.board)
