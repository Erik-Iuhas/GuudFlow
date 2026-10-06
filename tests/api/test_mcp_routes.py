"""Tests for /api/mcp/servers (Settings → MCP servers)."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

FAKE = str(Path(__file__).resolve().parent.parent / "mcp_fake.py")
SECRET = "super-secret-token-98765"


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("FAKE_TOKEN", raising=False)


def server_doc(**extra):
    return {"name": "fake", "transport": "stdio", "command": sys.executable, "args": [FAKE],
            "credential": {"env": "FAKE_TOKEN"}, **extra}


@pytest.mark.asyncio
async def test_requires_bearer(client):
    assert (await client.get("/api/mcp/servers")).status_code == 401
    assert (await client.post("/api/mcp/servers", json=server_doc())).status_code == 401
    assert (await client.put("/api/mcp/servers/fake/key", json={"key": SECRET})).status_code == 401
    assert (await client.post("/api/mcp/servers/fake/test")).status_code == 401
    assert (await client.post("/api/mcp/servers/import", json={})).status_code == 401


@pytest.mark.asyncio
async def test_crud(client, auth_header):
    assert (await client.get("/api/mcp/servers", headers=auth_header)).json() == {"servers": []}
    r = await client.post("/api/mcp/servers", json=server_doc(), headers=auth_header)
    assert r.status_code == 201 and r.json()["key"] == "missing"
    assert (await client.post("/api/mcp/servers", json=server_doc(), headers=auth_header)
            ).status_code == 409
    bad = await client.post("/api/mcp/servers", json=server_doc(name="Bad Name"),
                            headers=auth_header)
    assert bad.status_code == 422

    r = await client.put("/api/mcp/servers/fake", json=server_doc(enabled=False,
                         default_grants={"echo": ["builder"]}), headers=auth_header)
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await client.put("/api/mcp/servers/nope", json=server_doc(), headers=auth_header)
            ).status_code == 404
    bad_role = await client.put("/api/mcp/servers/fake", headers=auth_header,
                                json=server_doc(default_grants={"echo": ["wizard"]}))
    assert bad_role.status_code == 422

    assert (await client.delete("/api/mcp/servers/fake", headers=auth_header)).status_code == 200
    assert (await client.delete("/api/mcp/servers/fake", headers=auth_header)).status_code == 404


@pytest.mark.asyncio
async def test_secret_is_write_only_and_stored_0600(client, auth_header, data_root):
    await client.post("/api/mcp/servers", json=server_doc(), headers=auth_header)
    r = await client.put("/api/mcp/servers/fake/key", json={"key": SECRET}, headers=auth_header)
    assert r.status_code == 200 and r.json()["key"] == "stored"
    assert SECRET not in r.text
    for path in ("/api/mcp/servers",):
        assert SECRET not in (await client.get(path, headers=auth_header)).text
    keys = data_root / "secrets" / "mcp_keys.json"
    assert json.loads(keys.read_text()) == {"fake": SECRET}
    assert stat.S_IMODE(keys.stat().st_mode) == 0o600
    assert SECRET not in (data_root / "mcp_servers.json").read_text()

    # an environment variable wins and is reported as such
    import os
    os.environ["FAKE_TOKEN"] = "env-token-value-1"
    try:
        r = await client.get("/api/mcp/servers", headers=auth_header)
        assert r.json()["servers"][0]["key"] == "env" and "env-token-value-1" not in r.text
    finally:
        del os.environ["FAKE_TOKEN"]

    r = await client.delete("/api/mcp/servers/fake/key", headers=auth_header)
    assert r.json()["key"] == "missing" and not keys.read_text().count(SECRET)
    assert (await client.put("/api/mcp/servers/ghost/key", json={"key": SECRET},
                             headers=auth_header)).status_code == 404
    assert (await client.put("/api/mcp/servers/fake/key", json={"key": "a\nb"},
                             headers=auth_header)).status_code == 422


@pytest.mark.asyncio
async def test_test_route_lists_tools_and_caches_them(client, auth_header):
    await client.post("/api/mcp/servers", headers=auth_header,
                      json=server_doc(default_grants={"echo": ["builder"]}))
    # a declared credential that is not set fails cleanly, without a stack trace
    r = await client.post("/api/mcp/servers/fake/test", headers=auth_header)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "credential is not set" in r.json()["error"]

    await client.put("/api/mcp/servers/fake/key", json={"key": SECRET}, headers=auth_header)
    r = await client.post("/api/mcp/servers/fake/test", headers=auth_header)
    body = r.json()
    assert body["ok"] is True and body["tool_count"] == 4 and SECRET not in r.text
    assert [t["name"] for t in body["server"]["tools"]] == ["echo", "whoami", "leak", "boom"]
    assert body["server"]["default_grants"] == {"echo": ["builder"]}

    # cached tools survive an edit
    r = await client.put("/api/mcp/servers/fake", json=server_doc(), headers=auth_header)
    assert len(r.json()["tools"]) == 4
    assert (await client.post("/api/mcp/servers/ghost/test", headers=auth_header)
            ).status_code == 404


@pytest.mark.asyncio
async def test_import_mcp_json(client, auth_header, data_root, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    doc = {"mcpServers": {
        "github": {"command": "npx", "args": ["-y", "gh"],
                   "env": {"GITHUB_TOKEN": "ghp_literal_abc_123"}},
        "remote": {"url": "https://mcp.example.com/mcp",
                   "headers": {"X-Api-Key": "literal-header-secret", "X-Team": "blue"}},
        "bad": {}}}
    r = await client.post("/api/mcp/servers/import", json=doc, headers=auth_header)
    body = r.json()
    assert r.status_code == 200 and sorted(body["imported"]) == ["github", "remote"]
    assert body["skipped"][0]["name"] == "bad"
    names = {s["name"]: s for s in body["servers"]}
    assert names["github"]["key"] == "stored"
    assert names["remote"]["headers"] == {"X-Api-Key": "${credential}", "X-Team": "***"}
    assert names["remote"]["key"] == "stored"
    assert "ghp_literal_abc_123" not in r.text and "literal-header-secret" not in r.text
    assert "ghp_literal_abc_123" not in (data_root / "mcp_servers.json").read_text()

    again = await client.post("/api/mcp/servers/import", json=doc, headers=auth_header)
    assert again.json()["imported"] == []
    assert (await client.post("/api/mcp/servers/import", json={"mcpServers": 3},
                              headers=auth_header)).status_code == 422
