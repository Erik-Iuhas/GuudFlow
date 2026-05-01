"""AssignTask.run() rejects an unknown assignee with a helpful error."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agents.tools.board import AssignTask


def _make_tool(roster: set[str]) -> AssignTask:
    ws = MagicMock()
    ws.agent_roles = roster
    ws.active_story = {}
    tool = AssignTask(from_role="tech_lead", workspace=ws)
    return tool


@pytest.mark.asyncio
async def test_unknown_assignee_returns_error():
    tool = _make_tool({"dev_a", "reviewer"})
    result = await tool.run({"assignee": "ghost", "goal": "build the thing"})
    assert result.get("type") == "error" or "error" in str(result).lower()
    assert "ghost" in str(result)
    assert "dev_a" in str(result) or "reviewer" in str(result)


@pytest.mark.asyncio
async def test_known_assignee_does_not_error_on_roster_check():
    tool = _make_tool({"dev_a", "reviewer"})
    result = await tool.run({"assignee": "dev_a", "goal": "build the thing"})
    # Should not error out on roster validation (may fail elsewhere without full WS)
    if result.get("type") == "error":
        assert "unknown role" not in result.get("message", "")


@pytest.mark.asyncio
async def test_empty_roster_skips_validation():
    """When workspace.agent_roles is empty, skip roster check (backwards compat)."""
    tool = _make_tool(set())
    result = await tool.run({"assignee": "anyone", "goal": "do stuff"})
    # Should not error with unknown-role message
    if result.get("type") == "error":
        assert "unknown role" not in result.get("message", "")
