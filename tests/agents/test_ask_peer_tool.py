"""Tests for the ask-peer tool (peer-comms cleanup).

ask-peer is a discoverable wrapper around the M1 [ASK]/[ANSWER]
handshake. It returns a sentinel `{"_emit_protocol": "ASK", ...}`
that the runner's tool-use loop intercepts and routes through the
existing [ASK]-block handler — no new task instance, just suspend
the asker and dispatch the responder off-loop.
"""
from __future__ import annotations

import pytest

from agents.tools.ask_peer import AskPeer
from agents.tools.board import BoardMessage


@pytest.mark.asyncio
async def test_ask_peer_returns_emit_protocol_sentinel():
    tool = AskPeer()
    res = await tool.run({
        "to": "tech_lead",
        "question": "Should I use sqlite or postgres?",
    })
    assert res["_emit_protocol"] == "ASK"
    assert res["fields"]["to"] == "tech_lead"
    assert res["fields"]["question"] == "Should I use sqlite or postgres?"
    assert res["fields"]["context_paths"] == []


@pytest.mark.asyncio
async def test_ask_peer_carries_context_paths():
    tool = AskPeer()
    res = await tool.run({
        "to": "game_designer",
        "question": "Is the upgrade pool meant to refresh per level?",
        "context_paths": ["docs/design/02-upgrades.md", "src/upgrades/pool.py"],
    })
    assert res["fields"]["context_paths"] == [
        "docs/design/02-upgrades.md",
        "src/upgrades/pool.py",
    ]


@pytest.mark.asyncio
async def test_ask_peer_strips_whitespace_and_empties():
    """Defensive cleanup of inputs — agents sometimes pass paths with
    trailing whitespace or empty strings."""
    tool = AskPeer()
    res = await tool.run({
        "to": "  tech_lead  ",
        "question": "  hi  ",
        "context_paths": ["a.py", "", "  ", "b.py"],
    })
    assert res["fields"]["to"] == "tech_lead"
    assert res["fields"]["question"] == "hi"
    assert res["fields"]["context_paths"] == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_ask_peer_rejects_missing_to():
    res = await AskPeer().run({"question": "anything?"})
    assert "error" in res


@pytest.mark.asyncio
async def test_ask_peer_rejects_missing_question():
    res = await AskPeer().run({"to": "tech_lead"})
    assert "error" in res


@pytest.mark.asyncio
async def test_ask_peer_rejects_empty_strings():
    assert "error" in await AskPeer().run({"to": "", "question": "x"})
    assert "error" in await AskPeer().run({"to": "x", "question": ""})


@pytest.mark.asyncio
async def test_ask_peer_rejects_non_list_context_paths():
    res = await AskPeer().run({
        "to": "tech_lead",
        "question": "?",
        "context_paths": "not_a_list",
    })
    assert "error" in res


def test_ask_peer_priority_orders_above_board_message():
    """Catalog-rendering ordering check: ask-peer must sort BEFORE
    board-message so agents see the correct tool first when scanning
    the catalog for inter-agent communication."""
    assert AskPeer.priority < BoardMessage.priority


def test_ask_peer_when_to_use_under_120_chars():
    """Track 3d.4 catalog rendering cap."""
    assert len(AskPeer.when_to_use) <= 120


def test_ask_peer_in_baseline_scopes():
    """Every agent should have ask-peer available without explicit RBAC."""
    from core.tool_manifest import BASELINE_LOCAL_SCOPES
    assert "ask-peer" in BASELINE_LOCAL_SCOPES
