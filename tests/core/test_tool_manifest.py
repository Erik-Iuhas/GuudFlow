"""Tests for core/tool_manifest.py."""

from __future__ import annotations

import pytest

from core.tool_manifest import (
    BASELINE_LOCAL_SCOPES,
    LocalToolEntry,
    MCPToolEntry,
    RoleRBAC,
    ToolDescriptor,
    ToolManifest,
    ToolManifestBuilder,
)


# ── ToolDescriptor ─────────────────────────────────────────────────────────


def test_broker_descriptor_requires_scope():
    with pytest.raises(ValueError, match="must declare a scope"):
        ToolDescriptor(name="mcp-github-get_pr", description="x",
                       input_schema={}, invocation="broker", scope=None)


def test_local_descriptor_can_omit_scope():
    d = ToolDescriptor(name="fs-read", description="x", input_schema={},
                       invocation="local")
    assert d.scope is None


# ── RoleRBAC.grants ─────────────────────────────────────────────────────────


def test_grants_exact_match():
    r = RoleRBAC(role="dev", allowed_scopes=["fs-write"])
    assert r.grants("fs-write")


def test_grants_prefix_grants_subscope():
    r = RoleRBAC(role="dev", allowed_scopes=["mcp-github"])
    assert r.grants("mcp-github-read")
    assert r.grants("mcp-github-get_pr")


def test_specific_does_not_grant_sibling():
    r = RoleRBAC(role="dev", allowed_scopes=["mcp-github-read"])
    assert r.grants("mcp-github-read")
    assert not r.grants("mcp-github-write")


def test_no_scopes_grants_nothing():
    r = RoleRBAC(role="x")
    assert not r.grants("anything")


# ── ToolManifest ───────────────────────────────────────────────────────────


def test_manifest_find_returns_descriptor():
    m = ToolManifest([
        ToolDescriptor("fs-read", "read", {}, "local"),
        ToolDescriptor("fs-write", "write", {}, "local"),
    ])
    d = m.find("fs-read")
    assert d is not None and d.name == "fs-read"
    assert m.find("nope") is None


def test_manifest_iteration_and_names():
    m = ToolManifest([
        ToolDescriptor("a:x", "", {}, "local"),
        ToolDescriptor("a:y", "", {}, "local"),
    ])
    assert m.names() == ["a:x", "a:y"]
    assert len(m) == 2


def test_manifest_render_for_prompt_groups_by_category():
    m = ToolManifest([
        ToolDescriptor("fs-read", "Read a file",
                       {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                       "local"),
        ToolDescriptor("fs-write", "Write a file",
                       {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
                       "local"),
        ToolDescriptor("doc-pdf", "Extract text from PDF",
                       {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                       "local"),
        ToolDescriptor("mcp-github-get_pr", "Get a GitHub PR", {}, "broker",
                       scope="mcp-github-read", server="github"),
    ])
    text = m.render_for_prompt()
    assert "### fs" in text
    assert "### doc" in text
    assert "### mcp-github" in text
    assert "fs-read" in text
    assert "fs-write" in text
    assert "doc-pdf" in text
    assert "mcp-github-get_pr" in text
    # Schema summary present for one of them
    assert "path" in text


def test_manifest_render_empty_returns_empty():
    assert ToolManifest().render_for_prompt() == ""


# ── ToolManifestBuilder ────────────────────────────────────────────────────


@pytest.fixture
def builder() -> ToolManifestBuilder:
    local_registry = {
        "fs-read":  LocalToolEntry("fs-read",  "Read a file", {}),
        "fs-list":  LocalToolEntry("fs-list",  "List files", {}),
        "fs-write": LocalToolEntry("fs-write", "Write a file", {}),
        "fs-edit":  LocalToolEntry("fs-edit",  "Edit a file", {}),
        "code-exec": LocalToolEntry("code-exec", "Run code", {}),
        "git-diff":  LocalToolEntry("git-diff", "Show diff", {}),
    }
    mcp_catalogs = {
        "github": [
            MCPToolEntry("get_pr", "Get a PR", verb_scope="read"),
            MCPToolEntry("post_comment", "Post a comment", verb_scope="write"),
        ],
        "linear": [
            MCPToolEntry("create_issue", "Create an issue"),
        ],
    }
    rbac = {
        "developer": RoleRBAC(role="developer", allowed_scopes=[
            "fs-write", "fs-edit", "code-exec",
            "mcp-github-read",
        ]),
        "tech_writer": RoleRBAC(role="tech_writer", allowed_scopes=["mcp-github"]),
    }
    return ToolManifestBuilder(local_registry, mcp_catalogs, rbac)


def test_baseline_read_tools_always_included(builder: ToolManifestBuilder):
    """Read tools (fs:read, fs:list, git:diff, etc.) require no RBAC scope."""
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=["fs-read", "fs-list", "git-diff"],
        declared_mcp_servers=[],
    )
    names = m.names()
    assert "fs-read" in names
    assert "fs-list" in names
    assert "git-diff" in names


def test_write_tool_requires_explicit_rbac(builder: ToolManifestBuilder):
    """A role without fs:write scope cannot get fs:write even if declared."""
    builder.rbac["limited"] = RoleRBAC(role="limited", allowed_scopes=["fs-read"])
    m = builder.build_for_agent(
        role="limited",
        declared_local_tools=["fs-read", "fs-write"],
        declared_mcp_servers=[],
    )
    assert "fs-read" in m.names()
    assert "fs-write" not in m.names()


def test_developer_gets_declared_writes(builder: ToolManifestBuilder):
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=["fs-read", "fs-write", "fs-edit", "code-exec"],
        declared_mcp_servers=[],
    )
    assert {"fs-read", "fs-write", "fs-edit", "code-exec"} <= set(m.names())


def test_undeclared_local_tool_excluded(builder: ToolManifestBuilder):
    """If the agent didn't declare it, even a permitted tool isn't surfaced."""
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=["fs-read"],
        declared_mcp_servers=[],
    )
    assert "fs-write" not in m.names()


def test_unregistered_local_tool_silently_skipped(builder: ToolManifestBuilder):
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=["fs-read", "nonexistent:tool"],
        declared_mcp_servers=[],
    )
    assert "fs-read" in m.names()
    assert "nonexistent:tool" not in m.names()


def test_mcp_filtered_by_verb_scope(builder: ToolManifestBuilder):
    """developer has mcp:github:read — get_pr (verb=read) included, post_comment (verb=write) excluded."""
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=[],
        declared_mcp_servers=["github"],
    )
    names = m.names()
    assert "mcp-github-get_pr" in names
    assert "mcp-github-post_comment" not in names


def test_mcp_full_grant_includes_all_tools(builder: ToolManifestBuilder):
    """tech_writer has mcp:github (no verb suffix) — gets both tools."""
    m = builder.build_for_agent(
        role="tech_writer",
        declared_local_tools=[],
        declared_mcp_servers=["github"],
    )
    names = m.names()
    assert "mcp-github-get_pr" in names
    assert "mcp-github-post_comment" in names


def test_undeclared_mcp_server_excluded(builder: ToolManifestBuilder):
    """developer never declared linear — even if RBAC allowed, no tools."""
    builder.rbac["developer"].allowed_scopes.append("mcp-linear")
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=[],
        declared_mcp_servers=["github"],  # not linear
    )
    assert all(not n.startswith("mcp-linear") for n in m.names())


def test_role_with_no_rbac_entry_only_gets_baseline(builder: ToolManifestBuilder):
    """Default-deny: a role that's not in the RBAC matrix has no allowed_scopes."""
    m = builder.build_for_agent(
        role="unknown_role",
        declared_local_tools=["fs-read", "fs-write"],
        declared_mcp_servers=["github"],
    )
    # Baseline still works — fs:read is always-on.
    assert "fs-read" in m.names()
    # Write tools require explicit scope which an unknown role lacks.
    assert "fs-write" not in m.names()
    # MCP filtered out entirely.
    assert all(not n.startswith("mcp-") for n in m.names())


def test_baseline_auto_included_when_not_declared(builder: ToolManifestBuilder):
    """Baseline tools are auto-granted even when declared_local_tools is empty."""
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=[],
        declared_mcp_servers=[],
    )
    names = m.names()
    # These are in BASELINE_LOCAL_SCOPES and in the builder's local_registry
    assert "fs-read" in names
    assert "fs-list" in names
    assert "git-diff" in names
    # Non-baseline write tools must NOT be auto-included
    assert "fs-write" not in names
    assert "code-exec" not in names


def test_baseline_not_duplicated_when_declared(builder: ToolManifestBuilder):
    """Baseline tool declared explicitly does not appear twice."""
    m = builder.build_for_agent(
        role="developer",
        declared_local_tools=["fs-read"],
        declared_mcp_servers=[],
    )
    assert m.names().count("fs-read") == 1


def test_baseline_set_membership():
    assert "fs-read" in BASELINE_LOCAL_SCOPES
    assert "git-diff" in BASELINE_LOCAL_SCOPES
    assert "fs-write" not in BASELINE_LOCAL_SCOPES
    assert "code-exec" not in BASELINE_LOCAL_SCOPES
