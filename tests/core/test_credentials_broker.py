"""Tests for core/credentials_broker.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.credentials_broker import (
    AuditEntry,
    CredentialResult,
    CredentialsBroker,
    _redact_preview,
)
from core.tool_manifest import RoleRBAC


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def rbac():
    return {
        "developer": RoleRBAC(role="developer", allowed_scopes=[
            "mcp-github-read", "fs-write",
        ]),
        "tech_writer": RoleRBAC(role="tech_writer", allowed_scopes=[
            "mcp-github", "mcp-confluence-write",
        ]),
    }


@pytest.fixture
def broker(rbac, tmp_path):
    return CredentialsBroker(rbac=rbac, audit_log_path=tmp_path / "access.json")


# ── RBAC enforcement at request time ───────────────────────────────────────


async def test_default_deny_for_unknown_role(broker: CredentialsBroker):
    res = await broker.request("ghost", "mcp-github-read", "x", {})
    assert res.granted is False
    assert "ghost" in res.reason
    assert "mcp-github-read" in res.reason


async def test_default_deny_for_unscoped_call(broker: CredentialsBroker):
    res = await broker.request("developer", "mcp-slack-post", "x", {})
    assert res.granted is False
    assert "mcp-slack-post" in res.reason


async def test_grant_with_specific_scope(broker: CredentialsBroker):
    async def handler(op: str, payload: dict) -> dict:
        return {"echo": payload}
    broker.register_handler("mcp-github-read", handler)

    res = await broker.request("developer", "mcp-github-read", "get_pr", {"id": 1})
    assert res.granted is True
    assert res.result == {"echo": {"id": 1}}


async def test_prefix_scope_grants_subscope(broker: CredentialsBroker):
    """tech_writer has 'mcp-github' — covers 'mcp-github-write'."""
    async def handler(op: str, payload: dict) -> dict:
        return {"ok": True}
    broker.register_handler("mcp-github", handler)

    res = await broker.request("tech_writer", "mcp-github-write", "post_comment", {})
    assert res.granted is True


async def test_handler_resolves_prefix_when_specific_missing(broker: CredentialsBroker):
    """Specific scope check passes RBAC; broker falls back to prefix handler."""
    async def github_handler(op: str, payload: dict) -> dict:
        return {"server": "github"}
    broker.register_handler("mcp-github", github_handler)

    res = await broker.request("tech_writer", "mcp-github-read", "get_pr", {})
    assert res.granted is True
    assert res.result == {"server": "github"}


async def test_specific_handler_preferred_over_prefix(broker: CredentialsBroker):
    async def general_h(op: str, payload: dict) -> dict:
        return {"which": "general"}
    async def specific_h(op: str, payload: dict) -> dict:
        return {"which": "specific"}
    broker.register_handler("mcp-github", general_h)
    broker.register_handler("mcp-github-read", specific_h)

    res = await broker.request("tech_writer", "mcp-github-read", "get_pr", {})
    assert res.result == {"which": "specific"}


async def test_no_handler_denies(broker: CredentialsBroker):
    """RBAC allows but no handler registered → deny with clear reason."""
    res = await broker.request("developer", "mcp-github-read", "x", {})
    assert res.granted is False
    assert "no handler" in res.reason.lower()


async def test_handler_exception_denies(broker: CredentialsBroker):
    async def bad_handler(op: str, payload: dict) -> dict:
        raise RuntimeError("boom")
    broker.register_handler("mcp-github-read", bad_handler)

    res = await broker.request("developer", "mcp-github-read", "x", {})
    assert res.granted is False
    assert "handler error" in res.reason.lower()


async def test_request_id_round_trip(broker: CredentialsBroker):
    res = await broker.request("developer", "mcp-github-read", "x", {}, request_id="my-id")
    assert res.request_id == "my-id"


async def test_request_id_generated_when_absent(broker: CredentialsBroker):
    res = await broker.request("developer", "mcp-github-read", "x", {})
    assert res.request_id


# ── Audit log ──────────────────────────────────────────────────────────────


async def test_audit_records_grant(broker: CredentialsBroker):
    async def h(op: str, p: dict) -> dict:
        return {}
    broker.register_handler("mcp-github-read", h)

    await broker.request("developer", "mcp-github-read", "get_pr", {"id": 1})
    entries = broker.audit_entries()
    assert len(entries) == 1
    assert entries[0].decision == "granted"
    assert entries[0].from_agent == "developer"
    assert entries[0].scope == "mcp-github-read"


async def test_audit_records_denial_with_reason(broker: CredentialsBroker):
    await broker.request("developer", "mcp-slack-post", "send", {})
    entries = broker.audit_entries()
    assert len(entries) == 1
    assert entries[0].decision == "denied"
    assert entries[0].reason


async def test_audit_log_persists_to_disk(broker: CredentialsBroker, tmp_path: Path):
    async def h(op: str, p: dict) -> dict:
        return {}
    broker.register_handler("mcp-github-read", h)

    await broker.request("developer", "mcp-github-read", "x", {})
    await broker.request("developer", "mcp-nope", "x", {})

    log_file = tmp_path / "access.json"
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    entries = [json.loads(line) for line in lines]
    assert len(entries) == 2
    assert entries[0]["decision"] == "granted"
    assert entries[1]["decision"] == "denied"


async def test_audit_redacts_sensitive_keys(broker: CredentialsBroker):
    async def h(op: str, p: dict) -> dict:
        return {}
    broker.register_handler("mcp-github-read", h)

    await broker.request("developer", "mcp-github-read", "x",
                         {"path": "/foo", "auth_token": "supersecret"})
    entries = broker.audit_entries()
    preview = entries[0].redacted_payload_preview
    assert "auth_token=<redacted>" in preview
    assert "supersecret" not in preview
    assert "path=/foo" in preview


def test_redact_preview_handles_long_values():
    p = {"big": "x" * 200}
    out = _redact_preview(p)
    assert len(out) <= 100
    assert "…" in out


def test_redact_preview_empty():
    assert _redact_preview({}) == ""


# ── role_grants / scopes_for_role ──────────────────────────────────────────


def test_role_grants_via_broker(broker: CredentialsBroker):
    assert broker.role_grants("developer", "mcp-github-read")
    assert broker.role_grants("developer", "fs-write")
    assert not broker.role_grants("developer", "mcp-slack-post")
    assert not broker.role_grants("ghost", "anything")


def test_scopes_for_role(broker: CredentialsBroker):
    assert broker.scopes_for_role("developer") == ["mcp-github-read", "fs-write"]
    assert broker.scopes_for_role("ghost") == []
