"""Tests for /api/runs/{run_id}/cards (humans work the task board, S34)."""

from __future__ import annotations

import json

from core.harness.task_board import TaskBoard
from core.harness.tasks import PlanResult, PlannedTask
from core.workspace import Workspace
from worker.job_runner import serialise_task


def _register(app, run_id: str = "run_c") -> Workspace:
    ws = Workspace(run_id=run_id, goal="x")
    TaskBoard(ws).create_from_plan(PlanResult(tasks=[
        PlannedTask(id="T1", title="First card title", owner_role="backend",
                    acceptance=["it works"], size="S", kind="code"),
        PlannedTask(id="T2", title="Second card title", owner_role="frontend",
                    acceptance=["it renders"], size="M", kind="docs"),
    ]), "planner")
    app.state.run_registry.runs[run_id] = ws
    return ws


_NEW = {"owner": "backend", "title": "Add a health route", "acceptance": ["GET /health is 200"]}


async def test_get_cards_active(client, app) -> None:
    _register(app)
    r = await client.get("/api/runs/run_c/cards")
    assert r.status_code == 200
    cards = r.json()["cards"]
    assert [c["id"] for c in cards] == ["T1", "T2"]
    assert cards[0]["column"] == "todo" and cards[0]["acceptance"] == ["it works"]


async def test_get_cards_unknown_run_404(client) -> None:
    assert (await client.get("/api/runs/nope/cards")).status_code == 404


async def test_get_cards_finished_run_reads_tasks_jsonl(client, app, data_root) -> None:
    ws = _register(app, "run_done")
    # A legacy (non-board) task must not show up.
    from core.workspace import Task
    ws.add_task(Task(task_id="legacy1", story_id="s1", assigned_to="backend", goal="old"))
    run_root = data_root / "runs" / "run_done"
    run_root.mkdir(parents=True)
    lines = [json.dumps(serialise_task(t)) for t in ws.tasks.values()]
    (run_root / "tasks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    del app.state.run_registry.runs["run_done"]

    r = await client.get("/api/runs/run_done/cards")
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["cards"]] == ["T1", "T2"]


async def test_add_card_201(client, app, auth_header) -> None:
    ws = _register(app)
    r = await client.post("/api/runs/run_c/cards", json={**_NEW, "priority": 2},
                          headers=auth_header)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == "T3" and body["owner"] == "backend" and body["priority"] == 2
    assert body["column"] == "todo" and body["thread"][0]["text"] == "added by a human"
    assert "T3" in ws.tasks


async def test_add_card_size_l_400(client, app, auth_header) -> None:
    ws = _register(app)
    r = await client.post("/api/runs/run_c/cards", json={**_NEW, "size": "L"},
                          headers=auth_header)
    assert r.status_code == 400 and "split it" in r.json()["detail"]
    assert "T3" not in ws.tasks


async def test_add_card_unknown_owner_400(client, app, auth_header) -> None:
    _register(app)
    r = await client.post("/api/runs/run_c/cards", json={**_NEW, "owner": "ghost"},
                          headers=auth_header)
    assert r.status_code == 400


async def test_add_card_inactive_run_404(client, auth_header) -> None:
    r = await client.post("/api/runs/nope/cards", json=_NEW, headers=auth_header)
    assert r.status_code == 404


async def test_patch_priority(client, app, auth_header) -> None:
    ws = _register(app)
    r = await client.patch("/api/runs/run_c/cards/T2", json={"priority": 9},
                           headers=auth_header)
    assert r.status_code == 200 and r.json()["priority"] == 9
    assert ws.tasks["T2"].priority == 9
    assert TaskBoard(ws).next_for("frontend").task_id == "T2"


async def test_patch_unknown_card_404_and_done_400(client, app, auth_header) -> None:
    ws = _register(app)
    r = await client.patch("/api/runs/run_c/cards/T99", json={"priority": 1},
                           headers=auth_header)
    assert r.status_code == 404
    ws.tasks["T1"].column = "done"
    r = await client.patch("/api/runs/run_c/cards/T1", json={"priority": 1},
                           headers=auth_header)
    assert r.status_code == 400


async def test_patch_inactive_run_404(client, auth_header) -> None:
    r = await client.patch("/api/runs/nope/cards/T1", json={"priority": 1},
                           headers=auth_header)
    assert r.status_code == 404


async def test_comment_201_and_shows_in_next_brief(client, app, auth_header) -> None:
    ws = _register(app)
    r = await client.post("/api/runs/run_c/cards/T1/comments",
                          json={"text": "use the v2 endpoint"}, headers=auth_header)
    assert r.status_code == 201
    assert r.json()["kind"] == "human_comment" and r.json()["text"] == "use the v2 endpoint"
    assert "use the v2 endpoint" in TaskBoard(ws).brief_for("T1")


async def test_comment_errors(client, app, auth_header) -> None:
    _register(app)
    r = await client.post("/api/runs/run_c/cards/T1/comments", json={"text": "   "},
                          headers=auth_header)
    assert r.status_code == 400
    r = await client.post("/api/runs/run_c/cards/T99/comments", json={"text": "hi"},
                          headers=auth_header)
    assert r.status_code == 404
    r = await client.post("/api/runs/nope/cards/T1/comments", json={"text": "hi"},
                          headers=auth_header)
    assert r.status_code == 404


async def test_mutations_require_auth(client, app) -> None:
    _register(app)
    bad = {"Authorization": "Bearer wrong"}
    for headers in ({}, bad):
        assert (await client.post("/api/runs/run_c/cards", json=_NEW,
                                  headers=headers)).status_code == 401
        assert (await client.patch("/api/runs/run_c/cards/T1", json={"priority": 1},
                                   headers=headers)).status_code == 401
        assert (await client.post("/api/runs/run_c/cards/T1/comments", json={"text": "hi"},
                                  headers=headers)).status_code == 401


async def test_mutations_post_signals_for_websocket(client, app, auth_header) -> None:
    ws = _register(app)
    seen = []
    ws.subscribe(seen.append)
    await client.post("/api/runs/run_c/cards/T1/comments", json={"text": "hi"},
                      headers=auth_header)
    assert any(s.type.value == "TASK_THREAD" for s in seen)


async def test_a_card_can_go_to_a_team_role_that_has_not_started(client, auth_header, app) -> None:
    import json as _json

    from core.signals import Signal, SignalType
    ws = _register(app, "run_team")
    ws.post_signal(Signal(type=SignalType.TEAM, from_agent="runner", summary="the team",
                          detail=_json.dumps({"members": [
                              {"role": "developer", "role_type": "builder"},
                              {"role": "overseer", "role_type": "overseer"}]})))
    body = {"owner": "developer", "title": "Check directory", "acceptance": ["runs ls"],
            "kind": "code", "size": "S"}
    r = await client.post("/api/runs/run_team/cards", headers=auth_header, json=body)
    assert r.status_code in (200, 201), r.text
    r = await client.post("/api/runs/run_team/cards", headers=auth_header,
                          json={**body, "owner": "overseer"})
    assert r.status_code == 400
