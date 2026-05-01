"""Track 3e — manifest auto-grants batch variants when the parent is granted.

The piggyback rule: if a role has `fs-edit` granted (declared + RBAC),
they also see `fs-edit-batch` in their manifest without needing a
separate RBAC entry. Same for `fs-write` → `fs-write-batch`.
`code-outline-batch` is baseline (read-only, no RBAC needed).
"""
from __future__ import annotations

import pytest

from agents.tools.code import CodeOutline
from agents.tools.code_batch import CodeOutlineBatch
from agents.tools.fs import FsEdit, FsWrite
from agents.tools.fs_batch import FsEditBatch, FsWriteBatch
from core.tool_manifest import (
    LocalToolEntry,
    RoleRBAC,
    ToolManifestBuilder,
)


def _registry() -> dict[str, LocalToolEntry]:
    """Mini registry with the parent + batch tool entries."""
    out: dict[str, LocalToolEntry] = {}
    for cls in (FsEdit, FsWrite, FsEditBatch, FsWriteBatch, CodeOutline, CodeOutlineBatch):
        out[cls.name] = LocalToolEntry(
            name=cls.name,
            description=cls.description,
            input_schema=cls.input_schema,
        )
    return out


def _builder(allowed: list[str]) -> ToolManifestBuilder:
    return ToolManifestBuilder(
        local_registry=_registry(),
        mcp_catalogs={},
        rbac={"r": RoleRBAC(role="r", allowed_scopes=allowed)},
    )


def test_fs_edit_grant_includes_batch_variant():
    """Role with fs-edit declared + granted automatically gets fs-edit-batch."""
    builder = _builder(allowed=["fs-edit"])
    manifest = builder.build_for_agent(
        role="r",
        declared_local_tools=["fs-edit"],
        declared_mcp_servers=[],
    )
    names = {d.name for d in manifest.descriptors}
    assert "fs-edit" in names
    assert "fs-edit-batch" in names, "batch variant should auto-include with parent"


def test_fs_write_grant_includes_batch_variant():
    builder = _builder(allowed=["fs-write"])
    manifest = builder.build_for_agent(
        role="r",
        declared_local_tools=["fs-write"],
        declared_mcp_servers=[],
    )
    names = {d.name for d in manifest.descriptors}
    assert "fs-write" in names
    assert "fs-write-batch" in names


def test_no_fs_edit_grant_excludes_batch_variant():
    """Without the parent scope, the batch variant is NOT in the manifest."""
    builder = _builder(allowed=[])  # no scopes
    manifest = builder.build_for_agent(
        role="r",
        declared_local_tools=["fs-edit"],
        declared_mcp_servers=[],
    )
    names = {d.name for d in manifest.descriptors}
    assert "fs-edit" not in names
    assert "fs-edit-batch" not in names


def test_explicit_batch_declaration_uses_parent_scope():
    """A role that explicitly declares fs-edit-batch but only has fs-edit
    in RBAC should still get the batch variant — the batch tool's scope
    check piggybacks on the parent."""
    builder = _builder(allowed=["fs-edit"])
    manifest = builder.build_for_agent(
        role="r",
        declared_local_tools=["fs-edit-batch"],
        declared_mcp_servers=[],
    )
    names = {d.name for d in manifest.descriptors}
    assert "fs-edit-batch" in names


def test_code_outline_batch_is_baseline():
    """code-outline-batch is read-only and ships baseline like code-outline."""
    builder = _builder(allowed=[])
    manifest = builder.build_for_agent(
        role="r",
        declared_local_tools=[],
        declared_mcp_servers=[],
    )
    names = {d.name for d in manifest.descriptors}
    assert "code-outline" in names
    assert "code-outline-batch" in names
