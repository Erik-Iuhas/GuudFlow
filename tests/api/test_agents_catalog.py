"""Tests for /api/agents/catalog (Phase C.0)."""

from __future__ import annotations

import pytest

from agents.builtin import register_builtins


@pytest.fixture(autouse=True)
def _ensure_builtins():
    register_builtins()


async def test_get_catalog_returns_list_of_archetypes(client):
    r = await client.get("/api/agents/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "archetypes" in body
    names = {a["name"] for a in body["archetypes"]}
    assert "code_reviewer" in names
    assert "tech_lead" in names
    assert "requirements_writer" in names


async def test_catalog_entries_have_metadata(client):
    r = await client.get("/api/agents/catalog")
    body = r.json()
    by_name = {a["name"]: a for a in body["archetypes"]}
    rw = by_name["requirements_writer"]
    assert rw["abstract_base"] == "specialist"
    assert rw["default_model_tier"] in ("heavy", "solid", "fast")
    assert "doc:pdf" in rw["default_tools"]
    assert "fs:write" in rw["default_rbac_scopes"]
    assert rw["description"]
    assert rw["when_to_use"]


async def test_catalog_endpoint_does_not_require_auth(client):
    """Catalog is purely informational. No auth header required."""
    r = await client.get("/api/agents/catalog")
    assert r.status_code == 200


async def test_catalog_lookup_by_name(client):
    r = await client.get("/api/agents/catalog/tech_lead")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "tech_lead"
    assert body["abstract_base"] == "escalation_handler"
    assert body["default_model_tier"] == "heavy"


async def test_catalog_lookup_unknown_returns_404(client):
    r = await client.get("/api/agents/catalog/nonexistent")
    assert r.status_code == 404
