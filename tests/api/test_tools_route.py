"""Tests for GET /api/tools (M4 Chunk 1)."""
from __future__ import annotations

import pytest

from core.runner import LOCAL_TOOL_CLASSES
from core.tool_manifest import BASELINE_LOCAL_SCOPES


@pytest.mark.asyncio
async def test_tools_requires_bearer(client):
    resp = await client.get("/api/tools")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tools_count_matches_local_tool_classes(client, auth_header):
    resp = await client.get("/api/tools", headers=auth_header)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tools"]) == len(LOCAL_TOOL_CLASSES)


@pytest.mark.asyncio
async def test_tools_fs_read_is_baseline(client, auth_header):
    resp = await client.get("/api/tools", headers=auth_header)
    tools = {t["name"]: t for t in resp.json()["tools"]}
    assert "fs-read" in tools
    assert tools["fs-read"]["baseline"] is True
    assert tools["fs-read"]["scope"] == "fs-read"


@pytest.mark.asyncio
async def test_tools_assign_task_metadata(client, auth_header):
    resp = await client.get("/api/tools", headers=auth_header)
    tools = {t["name"]: t for t in resp.json()["tools"]}
    assert "assign-task" in tools
    entry = tools["assign-task"]
    assert entry["baseline"] is False
    assert entry["scope"] == "assign-task"
    granted = entry.get("granted_by_archetypes", [])
    assert "architect" in granted
    assert "requirements_writer" in granted
    assert "tech_lead" in granted


@pytest.mark.asyncio
async def test_tools_baseline_tools_match_constant(client, auth_header):
    resp = await client.get("/api/tools", headers=auth_header)
    for t in resp.json()["tools"]:
        expected = t["scope"] in BASELINE_LOCAL_SCOPES
        assert t["baseline"] == expected, (
            f"tool {t['name']}: baseline={t['baseline']} but scope in BASELINE_LOCAL_SCOPES={expected}"
        )
