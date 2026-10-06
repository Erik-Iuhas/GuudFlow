"""Tests for core/mcp_registry.py: the install-wide third-party MCP server registry."""

from __future__ import annotations

import json
import stat

import pytest

from core.harness.capabilities import (
    Capabilities,
    compile_capabilities,
    resolve_mcp_grants,
)
from core.mcp_registry import McpRegistry, McpRegistryError, namespaced, split_namespaced

STDIO = {"name": "files", "transport": "stdio", "command": "python3", "args": ["-m", "x"],
         "credential": {"env": "FILES_TOKEN"}}


@pytest.fixture
def reg(tmp_path):
    return McpRegistry(tmp_path)


def test_put_get_list_delete(reg):
    spec = reg.put(STDIO, create=True)
    assert spec.transport == "stdio" and reg.get("files") is not None
    assert [s.name for s in reg.list()] == ["files"]
    with pytest.raises(McpRegistryError, match="already exists"):
        reg.put(STDIO, create=True)
    with pytest.raises(McpRegistryError, match="unknown server"):
        reg.put({**STDIO, "name": "nope"}, create=False)
    assert reg.delete("files") is True and reg.delete("files") is False


@pytest.mark.parametrize("bad", [
    {**STDIO, "name": "Bad Name"}, {**STDIO, "name": "a__b"}, {**STDIO, "transport": "ws"},
    {**STDIO, "command": ""}, {"name": "x", "transport": "http", "url": "ftp://x"},
    {**STDIO, "credential": {"env": "not valid"}}, {**STDIO, "default_grants": {"t": ["wizard"]}},
])
def test_validation(reg, bad):
    with pytest.raises(McpRegistryError):
        reg.put(bad)


def test_secrets_stored_0600_and_status(reg, monkeypatch):
    monkeypatch.delenv("FILES_TOKEN", raising=False)
    spec = reg.put(STDIO)
    assert reg.key_status(spec) == "missing"
    reg.keys.set("files", "s3cret-value-123")
    assert reg.key_status(spec) == "stored" and reg.secret(spec) == "s3cret-value-123"
    path = reg.keys.path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    monkeypatch.setenv("FILES_TOKEN", "from-env-456")
    assert reg.key_status(spec) == "env" and reg.secret(spec) == "from-env-456"
    # the registry file itself never holds the secret
    assert "s3cret-value-123" not in reg.path.read_text()
    assert "from-env-456" not in json.dumps(reg.view(spec))
    assert reg.keys.clear("files") is True and reg.keys.get("files") is None
    no_cred = reg.put({"name": "plain", "transport": "stdio", "command": "x"})
    assert reg.key_status(no_cred) == "none"


def test_delete_forgets_secret(reg):
    reg.put(STDIO)
    reg.keys.set("files", "abcdef123456")
    reg.delete("files")
    assert reg.keys.get("files") is None


def test_set_tools_and_offers(reg):
    reg.put({**STDIO, "default_grants": {"echo": ["builder"], "gone": ["builder"]}})
    reg.set_tools("files", [{"name": "echo", "description": "d", "inputSchema": {"type": "object"}},
                            {"name": "other"}])
    spec = reg.get("files")
    assert [t.name for t in spec.tools] == ["echo", "other"]
    assert spec.default_grants == {"echo": ["builder"]}  # a vanished tool loses its grant
    assert reg.offers() == {"files__echo": frozenset({"builder"}), "files__other": frozenset()}
    reg.put({**STDIO, "enabled": False})  # editing keeps the cached tools, disabling hides them
    assert reg.offers() == {}
    assert [t.name for t in reg.get("files").tools] == ["echo", "other"]


def test_literal_headers_masked_in_view_and_kept_on_put(reg):
    reg.put({"name": "api", "transport": "http", "url": "https://x.test/mcp",
             "headers": {"X-Api-Key": "literal-secret", "X-Env": "${FOO}"}})
    view = reg.view(reg.get("api"))
    assert view["headers"] == {"X-Api-Key": "***", "X-Env": "${FOO}"}
    reg.put({**view, "enabled": True})
    assert reg.get("api").headers["X-Api-Key"] == "literal-secret"


def test_import_mcp_json(reg, monkeypatch):
    doc = {"mcpServers": {
        "GitHub": {"command": "npx", "args": ["-y", "gh-mcp"],
                   "env": {"GITHUB_TOKEN": "ghp_literal_token_1", "LOG": "${LOG}"}},
        "remote": {"url": "https://mcp.example.com/mcp",
                   "headers": {"Authorization": "Bearer literal-bearer-xyz"}},
        "env-ref": {"url": "https://e.example.com/mcp",
                    "headers": {"Authorization": "Bearer ${EXAMPLE_TOKEN}"}},
        "broken": {"nothing": True},
    }}
    result = reg.import_servers(doc)
    assert sorted(result["imported"]) == ["env-ref", "github", "remote"]
    assert result["skipped"][0]["name"] == "broken"
    gh = reg.get("github")
    assert gh.transport == "stdio" and gh.args == ["-y", "gh-mcp"]
    assert gh.credential == {"env": "GITHUB_TOKEN"} and gh.env == ["LOG"]
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert reg.secret(gh) == "ghp_literal_token_1"
    remote = reg.get("remote")
    assert remote.headers == {"Authorization": "Bearer ${credential}"}
    assert reg.secret(remote) == "literal-bearer-xyz"
    # no literal secret ever reaches the registry file
    text = reg.path.read_text()
    assert "ghp_literal_token_1" not in text and "literal-bearer-xyz" not in text
    # existing names are skipped unless replacing
    again = reg.import_servers(doc)
    assert {s["name"] for s in again["skipped"]} >= {"github", "remote", "env-ref"}


def test_import_opencode_block(reg):
    result = reg.import_servers({"mcp": {
        "docs": {"type": "local", "command": ["node", "docs.js", "--x"], "enabled": False},
        "web": {"type": "remote", "url": "https://w.example.com/mcp",
                "headers": {"X-Key": "{env:WEB_KEY}"}}}})
    assert sorted(result["imported"]) == ["docs", "web"]
    docs = reg.get("docs")
    assert (docs.command, docs.args, docs.enabled) == ("node", ["docs.js", "--x"], False)
    assert reg.get("web").headers == {"X-Key": "${WEB_KEY}"}


def test_import_rejects_garbage(reg):
    with pytest.raises(McpRegistryError):
        reg.import_servers({"mcpServers": "nope"})


def test_namespacing():
    assert namespaced("gh", "search") == "gh__search"
    assert split_namespaced("gh__search__x") == ("gh", "search__x")
    assert split_namespaced("post_note") is None


def test_resolve_mcp_grants_default_deny_and_narrowing():
    offers = {"gh__search": frozenset({"builder", "researcher"}), "gh__write": frozenset()}
    # no explicit list: registry defaults for the role type
    assert resolve_mcp_grants(None, "builder", offers) == {"gh__search"}
    assert resolve_mcp_grants(None, "reviewer", offers) == frozenset()
    # explicit list: only what the registry actually offers
    assert resolve_mcp_grants(["gh__write", "gh__ghost", "web_search"], "reviewer", offers) \
        == {"gh__write"}
    assert resolve_mcp_grants([], "builder", offers) == frozenset()


def test_compile_capabilities_includes_extra_tools():
    c = compile_capabilities(Capabilities(), "builder", extra_mcp_tools=["gh__search", "plain"])
    assert "gh__search" in c.mcp_tools and "plain" not in c.mcp_tools
    base = compile_capabilities(Capabilities(), "builder")
    assert c.digest() != base.digest()
