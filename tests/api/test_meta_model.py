"""Tests for meta_model parameter on POST /api/meta/propose (M4 Chunk 2)."""
from __future__ import annotations

import pytest

from agents.builtin import register_builtins
from core.llm import MockLLMClient, MockTurn, ToolCall


@pytest.fixture(autouse=True)
def _ensure_builtins():
    register_builtins()


def _mock_llm():
    return MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="m1", name="propose_team",
        arguments={
            "rationale": "need a dev",
            "agents": [{
                "role": "dev",
                "archetype": "backend_dev",
                "depends_on": [],
                "persona": {"action": "create_new", "name_hint": "Alice"},
            }],
        },
    )])])


@pytest.mark.asyncio
async def test_meta_model_absent_uses_existing_behaviour(client, auth_header, app):
    """No meta_model → falls through to existing _build_meta_llm path."""
    app.state.meta_llm = _mock_llm()
    resp = await client.post(
        "/api/meta/propose",
        json={"goal": "build something"},
        headers=auth_header,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_meta_model_invalid_returns_400(client, auth_header, app):
    app.state.meta_llm = _mock_llm()
    resp = await client.post(
        "/api/meta/propose",
        json={"goal": "build something", "meta_model": "opus-4"},
        headers=auth_header,
    )
    assert resp.status_code == 400
    assert "meta_model" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_meta_model_solid_accepted(client, auth_header, app):
    """meta_model='solid' is a valid tier — request succeeds."""
    app.state.meta_llm = _mock_llm()
    resp = await client.post(
        "/api/meta/propose",
        json={"goal": "build something", "meta_model": "solid"},
        headers=auth_header,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_meta_model_all_valid_tiers_accepted(client, auth_header, app):
    for tier in ("heavy", "solid", "fast"):
        app.state.meta_llm = _mock_llm()
        resp = await client.post(
            "/api/meta/propose",
            json={"goal": "build something", "meta_model": tier},
            headers=auth_header,
        )
        assert resp.status_code == 200, f"tier={tier} failed: {resp.text}"
