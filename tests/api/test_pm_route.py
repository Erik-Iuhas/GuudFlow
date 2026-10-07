"""/api/pm and the episodes the plan routes write into Jack's memory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.llm.client import MockLLMClient, MockTurn, ToolCall
from core.pm_persona import pm_dir
from core.project_manager import TOOL_NAME
from tests.spec_fixtures import with_reasons

REQ = {"op": "add_requirement", "ref": "new:1", "text": "Save a recipe", "kind": "feature",
       "priority": "must", "checks": ["a saved recipe shows in the list"]}


def _turn(*changes: dict[str, Any]) -> MockTurn:
    return MockTurn(tool_calls=[ToolCall("1", TOOL_NAME, with_reasons({
        "reply": "ok", "changes": list(changes)}))])


async def _project(client, auth_header) -> None:
    r = await client.post("/api/projects", json={"project_id": "p1", "goal": "A recipe website"},
                          headers=auth_header)
    assert r.status_code in (200, 201)


def _episodes(data_root: Path) -> list[dict[str, Any]]:
    path = pm_dir(data_root) / "episodes.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


async def test_pm_needs_the_token(client) -> None:
    assert (await client.get("/api/pm")).status_code == 401


async def test_pm_summary_creates_jack(client, auth_header) -> None:
    r = await client.get("/api/pm", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert body["persona_id"] == "jack-project-manager-v1" and body["name"] == "Jack"
    assert body["specialty"] == "project manager"
    assert body["episodes"] == 0 and body["patterns"] == 0
    assert body["reputation"] == {"sessions_accepted": 0, "sessions_rejected": 0,
                                  "drafts_approved": 0, "drafts_approved_untouched": 0,
                                  "drafts_discarded": 0}


async def test_approve_and_discard_write_episodes(client, auth_header, app, data_root) -> None:
    await _project(client, auth_header)
    app.state.meta_llm = MockLLMClient([_turn(REQ), _turn(REQ)])
    await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                      json={"text": "Save recipes", "mode": "update"})
    await client.patch("/api/projects/p1/plan/draft/c1", headers=auth_header,
                       json={"accepted": True})
    assert (await client.post("/api/projects/p1/plan/draft/approve",
                              headers=auth_header)).status_code == 200

    await client.post("/api/projects/p1/plan/messages", headers=auth_header,
                      json={"text": "More", "mode": "update"})
    assert (await client.post("/api/projects/p1/plan/draft/discard",
                              headers=auth_header)).status_code == 200

    first, second = _episodes(data_root)
    assert "approved" in first["tags"] and "web" in first["tags"]
    assert first["what_worked"].endswith("c1:add_requirement")
    assert "discarded" in second["tags"] and "c1:add_requirement" in second["what_failed"]

    body = (await client.get("/api/pm", headers=auth_header)).json()
    assert body["episodes"] == 2
    assert body["reputation"]["drafts_approved_untouched"] == 1
    assert body["reputation"]["drafts_discarded"] == 1


async def test_jack_shows_in_the_roster(client, auth_header) -> None:
    await client.get("/api/pm", headers=auth_header)
    r = await client.get("/api/personas", headers=auth_header)
    assert r.status_code == 200
    rows = r.json()
    rows = rows.get("personas", rows) if isinstance(rows, dict) else rows
    jack = next(p for p in rows if p.get("persona_id") == "jack-project-manager-v1")
    assert jack["name"] == "Jack"
