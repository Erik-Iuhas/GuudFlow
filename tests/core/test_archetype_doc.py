"""Tests for core/archetype_doc.py — YAML archetype loader (Phase D.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.archetype_doc import (
    ArchetypeDocEntry,
    ArchetypeDocError,
    get_doc_archetype,
    load_archetypes,
)


# ── Default YAML loads ─────────────────────────────────────────────────────


def test_default_yaml_loads():
    """The shipped docs/worker-archetypes.yaml parses without error."""
    entries = load_archetypes()
    assert len(entries) > 0


def test_default_yaml_includes_shipped_archetypes():
    """All four shipped archetypes (code_reviewer, tech_lead,
    requirements_writer, architect) appear in the YAML with shipped=true."""
    entries = load_archetypes()
    by_name = {e.name: e for e in entries}
    for name in ("code_reviewer", "tech_lead", "requirements_writer", "architect"):
        assert name in by_name, f"{name} missing from YAML"
        assert by_name[name].shipped is True


def test_default_yaml_includes_described_archetypes():
    """The YAML has more than just the shipped ones — it's the
    brainstorming surface, not just a runtime mirror."""
    entries = load_archetypes()
    described = [e for e in entries if not e.shipped]
    assert len(described) >= 5  # we shipped 11 described in the initial draft


def test_tech_lead_carries_wake_signal():
    """Escalation handlers declare what signal type wakes them."""
    e = get_doc_archetype("tech_lead")
    assert e is not None
    assert e.wake_on_signal_type == "REJECTED"


def test_specialist_has_no_wake_signal():
    """Specialists/reviewers don't have wake_on_signal_type."""
    e = get_doc_archetype("requirements_writer")
    assert e is not None
    assert e.wake_on_signal_type is None


def test_to_meta_dict_projects_to_catalog_shape():
    """The doc entry projects down to the same dict shape the runtime
    catalog endpoint emits."""
    e = get_doc_archetype("code_reviewer")
    assert e is not None
    d = e.to_meta_dict()
    # Same keys as ArchetypeMeta.to_dict()
    for key in ("name", "abstract_base", "description", "when_to_use",
                "default_model_tier", "default_tools", "default_rbac_scopes"):
        assert key in d
    # Description is the first line of the responsibility block.
    assert "senior code reviewer" in d["description"].lower()


# ── Parser error cases ─────────────────────────────────────────────────────


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "archetypes.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_missing_file_returns_empty_list(tmp_path: Path):
    """Defensive: missing YAML file → no archetypes, no crash."""
    assert load_archetypes(tmp_path / "nope.yaml") == []


def test_top_level_must_be_mapping(tmp_path: Path):
    p = _write_yaml(tmp_path, "- just_a_list")
    with pytest.raises(ArchetypeDocError, match="top-level YAML"):
        load_archetypes(p)


def test_archetypes_must_be_list(tmp_path: Path):
    p = _write_yaml(tmp_path, "archetypes: not_a_list")
    with pytest.raises(ArchetypeDocError, match="must be a list"):
        load_archetypes(p)


def test_missing_required_field_raises(tmp_path: Path):
    """Missing `default_tools` should fail loudly so config drift is visible."""
    p = _write_yaml(tmp_path, """
archetypes:
  - name: foo
    abstract_base: specialist
    shipped: false
    default_model_tier: solid
    # missing default_tools
    default_rbac_scopes: []
    responsibility: x
    when_to_use: y
""")
    with pytest.raises(ArchetypeDocError, match="default_tools"):
        load_archetypes(p)


def test_invalid_abstract_base_raises(tmp_path: Path):
    p = _write_yaml(tmp_path, """
archetypes:
  - name: foo
    abstract_base: orchestrator
    shipped: false
    default_model_tier: solid
    default_tools: []
    default_rbac_scopes: []
    responsibility: x
    when_to_use: y
""")
    with pytest.raises(ArchetypeDocError, match="abstract_base"):
        load_archetypes(p)


def test_invalid_tier_raises(tmp_path: Path):
    p = _write_yaml(tmp_path, """
archetypes:
  - name: foo
    abstract_base: specialist
    shipped: false
    default_model_tier: ultra
    default_tools: []
    default_rbac_scopes: []
    responsibility: x
    when_to_use: y
""")
    with pytest.raises(ArchetypeDocError, match="default_model_tier"):
        load_archetypes(p)


def test_minimal_valid_entry_loads(tmp_path: Path):
    """A minimal but valid YAML entry parses cleanly."""
    p = _write_yaml(tmp_path, """
archetypes:
  - name: foo
    abstract_base: specialist
    shipped: false
    default_model_tier: fast
    default_tools: []
    default_rbac_scopes: []
    responsibility: do foo
    when_to_use: when foo
""")
    entries = load_archetypes(p)
    assert len(entries) == 1
    assert entries[0].name == "foo"
    assert entries[0].abstract_base == "specialist"
    assert entries[0].default_tools == []


def test_get_doc_archetype_returns_none_for_unknown():
    assert get_doc_archetype("nonexistent_archetype") is None
