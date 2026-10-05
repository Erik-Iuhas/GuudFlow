"""Tests for /api/model-config (Settings → Models & providers)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from core import model_config

SECRET = "sk-test-secret-value-0123456789"


@pytest.fixture(autouse=True)
def no_provider_env(monkeypatch):
    doc = model_config.load(None)
    for p in doc["providers"].values():
        monkeypatch.delenv(p["api_key_env"], raising=False)


@pytest.mark.asyncio
async def test_requires_bearer(client):
    assert (await client.get("/api/model-config")).status_code == 401
    r = await client.put("/api/model-config/providers/anthropic/key", json={"key": SECRET})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_defaults_then_save_then_reset(client, auth_header):
    r = await client.get("/api/model-config", headers=auth_header)
    body = r.json()
    assert r.status_code == 200 and body["custom"] is False
    assert {p["name"] for p in body["providers"]} >= {"anthropic", "openai"}
    assert all(p["key"] in ("missing", "local") for p in body["providers"])

    doc = {"providers": {p["name"]: {k: p[k] for k in ("client", "base_url", "api_key_env")}
                         for p in body["providers"]},
           "models": body["models"], "tiers": {"heavy": "claude-opus-5-5"}}
    r = await client.put("/api/model-config", json=doc, headers=auth_header)
    assert r.status_code == 200 and r.json()["custom"] is True
    assert r.json()["tiers"] == {"heavy": "claude-opus-5-5"}

    doc["tiers"] = {"heavy": "no-such-model"}
    r = await client.put("/api/model-config", json=doc, headers=auth_header)
    assert r.status_code == 422 and "unknown model" in r.json()["detail"]

    r = await client.post("/api/model-config/reset", headers=auth_header)
    assert r.json()["custom"] is False


@pytest.mark.asyncio
async def test_keys_are_write_only(client, auth_header, data_root: Path):
    r = await client.put("/api/model-config/providers/anthropic/key", json={"key": SECRET},
                         headers=auth_header)
    assert r.status_code == 200
    assert SECRET not in r.text
    status = {p["name"]: p["key"] for p in r.json()["providers"]}
    assert status["anthropic"] == "stored"
    assert json.loads((data_root / "secrets" / "provider_keys.json").read_text())["anthropic"] == SECRET
    assert SECRET not in (await client.get("/api/model-config", headers=auth_header)).text

    r = await client.delete("/api/model-config/providers/anthropic/key", headers=auth_header)
    assert {p["name"]: p["key"] for p in r.json()["providers"]}["anthropic"] == "missing"
    r = await client.put("/api/model-config/providers/nope/key", json={"key": SECRET},
                         headers=auth_header)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_provider_test_uses_the_stored_key(client, auth_header, monkeypatch):
    seen: dict[str, str] = {}

    async def fake_get(self, url, headers=None, **_):
        seen["url"] = str(url)
        seen["key"] = (headers or {}).get("x-api-key", "")
        return httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}, {"id": "claude-haiku-4-5"}]})

    r = await client.post("/api/model-config/providers/anthropic/test", headers=auth_header)
    assert r.json()["ok"] is False and "no key" in r.json()["detail"]

    await client.put("/api/model-config/providers/anthropic/key", json={"key": SECRET},
                     headers=auth_header)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    r = await client.post("/api/model-config/providers/anthropic/test", headers=auth_header)
    body = r.json()
    assert body["ok"] is True and body["models"] == ["claude-haiku-4-5", "claude-opus-5-5"]
    assert seen == {"url": "https://api.anthropic.com/v1/models", "key": SECRET}
    assert SECRET not in r.text
