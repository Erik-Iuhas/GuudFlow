"""Tests for /api/harness (S17)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.harness.capabilities import HOST_DEV_WARNING, LEVELS, ROLE_TYPES

VISION_MODEL = "claude-sonnet-5-5"
TEXT_MODEL = "deepseek-chat"


@pytest.fixture(autouse=True)
def models_file(monkeypatch):
    """Use the repo's models.json regardless of the test's working directory."""
    import api.routes.harness as h

    path = Path(__file__).resolve().parents[2] / "data" / "models.json"
    monkeypatch.setattr(h, "_REGISTRY_PATH", path)


@pytest.mark.asyncio
async def test_requires_bearer(client):
    assert (await client.get("/api/harness/capabilities")).status_code == 401
    r = await client.post("/api/harness/capabilities/resolve", json={"role": "x"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_capabilities(client, auth_header, monkeypatch):
    monkeypatch.delenv("GUUDFLOW_ENV", raising=False)
    data = (await client.get("/api/harness/capabilities", headers=auth_header)).json()
    assert data["levels"] == {k: list(v) for k, v in LEVELS.items()}
    assert data["role_types"] == list(ROLE_TYPES)
    assert set(data["defaults"]) == set(ROLE_TYPES)
    assert data["defaults"]["builder"]["shell"] == "project"
    assert data["host_dev_warning"] == HOST_DEV_WARNING
    assert data["production"] is False


@pytest.mark.asyncio
async def test_production_flag(client, auth_header, monkeypatch):
    monkeypatch.setenv("GUUDFLOW_ENV", "production")
    data = (await client.get("/api/harness/capabilities", headers=auth_header)).json()
    assert data["production"] is True


@pytest.mark.asyncio
async def test_resolve_infers_role_type_and_defaults(client, auth_header):
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header,
                          json={"role": "code_checker", "is_reviewer": True})
    body = r.json()
    assert r.status_code == 200
    assert body["role_type"] == "reviewer"
    assert body["capabilities"]["browser"] == "screenshot"
    assert body["vision_available"] is True
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_resolve_merges_overrides(client, auth_header):
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header, json={
        "role": "dev", "role_type": "builder", "capabilities": {"web": "search"}})
    caps = r.json()["capabilities"]
    assert caps["web"] == "search"
    assert caps["shell"] == "project"  # default kept


@pytest.mark.asyncio
async def test_resolve_vision_gated_by_model(client, auth_header):
    body = {"role": "ui", "role_type": "frontend_builder"}
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header,
                          json={**body, "model_id": TEXT_MODEL})
    data = r.json()
    assert data["vision_available"] is False
    assert data["capabilities"]["vision"] == "off"
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header,
                          json={**body, "model_id": VISION_MODEL})
    data = r.json()
    assert data["vision_available"] is True
    assert data["capabilities"]["vision"] == "on"


@pytest.mark.asyncio
async def test_resolve_invalid_value_422(client, auth_header):
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header, json={
        "role": "dev", "capabilities": {"web": "everything"}})
    assert r.status_code == 422
    assert "web" in r.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_unknown_toggle_422(client, auth_header):
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header, json={
        "role": "dev", "capabilities": {"nope": "on"}})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_resolve_unknown_role_type_422(client, auth_header):
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header, json={
        "role": "dev", "role_type": "wizard"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_resolve_host_dev(client, auth_header, monkeypatch):
    body = {"role": "dev", "capabilities": {"sandbox": "host_dev"}}
    monkeypatch.delenv("GUUDFLOW_ENV", raising=False)
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header, json=body)
    assert r.status_code == 200
    assert HOST_DEV_WARNING in r.json()["warnings"]
    monkeypatch.setenv("GUUDFLOW_ENV", "production")
    r = await client.post("/api/harness/capabilities/resolve", headers=auth_header, json=body)
    assert r.status_code == 422
    assert "production" in r.json()["detail"]
