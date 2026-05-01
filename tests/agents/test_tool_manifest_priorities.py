"""Track 3d.4 — manifest renders tools sorted by priority with when_to_use hints.

The tool catalog (Layer 5 of effective_system_prompt) used to group tools
by category prefix. Now it sorts globally by `priority` and surfaces a
`when_to_use` hint per tool — so the LLM sees the funnel order directly
without inferring it from category names.
"""
from __future__ import annotations

import pytest

from core.tool_manifest import (
    LocalToolEntry,
    RoleRBAC,
    ToolDescriptor,
    ToolManifest,
    ToolManifestBuilder,
)


def _entry(name: str, *, priority: int = 100, when_to_use: str = "") -> LocalToolEntry:
    return LocalToolEntry(
        name=name,
        description=f"description of {name}",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        priority=priority,
        when_to_use=when_to_use,
    )


def _builder(entries: list[LocalToolEntry], allowed: list[str]) -> ToolManifestBuilder:
    return ToolManifestBuilder(
        local_registry={e.name: e for e in entries},
        mcp_catalogs={},
        rbac={"r": RoleRBAC(role="r", allowed_scopes=allowed)},
    )


# ── Sorting ────────────────────────────────────────────────────────────────


def test_descriptors_sorted_by_priority_ascending():
    """Lower priority number renders first; the order encodes the funnel."""
    manifest = ToolManifest(descriptors=[
        ToolDescriptor(name="z-late", description="z", input_schema={}, invocation="local",
                       priority=100, when_to_use=""),
        ToolDescriptor(name="a-early", description="a", input_schema={}, invocation="local",
                       priority=20, when_to_use=""),
        ToolDescriptor(name="m-mid", description="m", input_schema={}, invocation="local",
                       priority=60, when_to_use=""),
    ])
    rendered = manifest.render_for_prompt()
    pos_a = rendered.index("a-early")
    pos_m = rendered.index("m-mid")
    pos_z = rendered.index("z-late")
    assert pos_a < pos_m < pos_z


def test_render_includes_priority_prefix():
    """Each line starts with [priority] so the LLM sees the ordering signal."""
    manifest = ToolManifest(descriptors=[
        ToolDescriptor(name="t1", description="d1", input_schema={}, invocation="local",
                       priority=20, when_to_use=""),
        ToolDescriptor(name="t2", description="d2", input_schema={}, invocation="local",
                       priority=105, when_to_use=""),
    ])
    rendered = manifest.render_for_prompt()
    assert "[ 20]" in rendered
    assert "[105]" in rendered


def test_tied_priority_falls_back_to_alphabetical():
    """Two tools at the same priority sort by name for deterministic output."""
    manifest = ToolManifest(descriptors=[
        ToolDescriptor(name="b-tied", description="b", input_schema={}, invocation="local",
                       priority=100, when_to_use=""),
        ToolDescriptor(name="a-tied", description="a", input_schema={}, invocation="local",
                       priority=100, when_to_use=""),
    ])
    rendered = manifest.render_for_prompt()
    assert rendered.index("a-tied") < rendered.index("b-tied")


# ── when_to_use rendering ──────────────────────────────────────────────────


def test_when_to_use_appears_when_set():
    manifest = ToolManifest(descriptors=[
        ToolDescriptor(name="t1", description="raw description", input_schema={},
                       invocation="local", priority=100,
                       when_to_use="prefer this when X"),
    ])
    rendered = manifest.render_for_prompt()
    assert "prefer this when X" in rendered


def test_when_to_use_omitted_when_empty():
    """An empty when_to_use renders as just the description — no blank hint line."""
    manifest = ToolManifest(descriptors=[
        ToolDescriptor(name="t1", description="raw description", input_schema={},
                       invocation="local", priority=100, when_to_use=""),
    ])
    rendered = manifest.render_for_prompt()
    assert "raw description" in rendered
    # No empty `— ` followed by another newline (suggests an empty hint).
    # Easier check: no double newline immediately after the tool name.
    lines = rendered.splitlines()
    tool_line_idx = next(i for i, ln in enumerate(lines) if "t1" in ln)
    assert lines[tool_line_idx].endswith("raw description")


# ── Empty manifest ─────────────────────────────────────────────────────────


def test_empty_manifest_returns_empty_string():
    manifest = ToolManifest(descriptors=[])
    assert manifest.render_for_prompt() == ""


# ── Plumbing through the builder ───────────────────────────────────────────


def test_priority_propagates_from_entry_to_descriptor():
    """When the builder constructs a descriptor, it copies priority and when_to_use
    from the registry entry (which itself was populated from the tool class)."""
    entries = [_entry("fs-edit", priority=105, when_to_use="single edit; prefer batch for ≥ 2"),
               _entry("fs-edit-batch", priority=100, when_to_use="PREFER for ≥ 2 edits")]
    builder = _builder(entries, allowed=["fs-edit"])
    manifest = builder.build_for_agent(
        role="r",
        declared_local_tools=["fs-edit"],
        declared_mcp_servers=[],
    )
    by_name = {d.name: d for d in manifest.descriptors}
    assert by_name["fs-edit"].priority == 105
    assert "prefer batch" in by_name["fs-edit"].when_to_use
    assert by_name["fs-edit-batch"].priority == 100
    assert "PREFER" in by_name["fs-edit-batch"].when_to_use


def test_full_render_shape_with_funnel_signal():
    """Realistic mini-catalog: outline before read before edit before edit-batch.

    The point: rendered order must match the priority numbers, so an LLM
    scanning top-down sees the funnel naturally.
    """
    manifest = ToolManifest(descriptors=[
        ToolDescriptor(name="code-outline", description="signatures", input_schema={},
                       invocation="local", priority=20, when_to_use="before fs-read"),
        ToolDescriptor(name="fs-read", description="full body", input_schema={},
                       invocation="local", priority=60, when_to_use="when you need bodies"),
        ToolDescriptor(name="fs-edit-batch", description="batch edit", input_schema={},
                       invocation="local", priority=100,
                       when_to_use="PREFER for ≥ 2 edits"),
        ToolDescriptor(name="fs-edit", description="single edit", input_schema={},
                       invocation="local", priority=105,
                       when_to_use="single edit; prefer batch for ≥ 2"),
    ])
    rendered = manifest.render_for_prompt()
    # The funnel: outline → read → batch-edit → single-edit.
    pos = [rendered.index(name) for name in
           ("code-outline", "fs-read", "fs-edit-batch", "fs-edit")]
    assert pos == sorted(pos)
    # Header announces the rule.
    assert "lower number = reach first" in rendered
