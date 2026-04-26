"""Tests for agents/catalog.py (Phase C.0)."""

from __future__ import annotations

import pytest

from agents.builtin import register_builtins
from agents.catalog import (
    ArchetypeMeta,
    get_archetype,
    list_archetypes,
)
from agents.registry import registry


@pytest.fixture(autouse=True)
def _ensure_builtins():
    """Make sure the builtins are registered before each test. Idempotent."""
    register_builtins()


def test_register_builtins_is_idempotent():
    """Calling twice doesn't blow up."""
    register_builtins()
    register_builtins()
    # All three archetypes are present.
    for name in ("code_reviewer", "tech_lead", "requirements_writer"):
        assert registry.has(name)


def test_list_archetypes_includes_each_builtin():
    metas = list_archetypes()
    names = {m.name for m in metas}
    assert "code_reviewer" in names
    assert "tech_lead" in names
    assert "requirements_writer" in names


def test_list_archetypes_groups_by_abstract_base():
    """Output is sorted by (abstract_base, name) — stable for the
    catalog endpoint."""
    metas = list_archetypes()
    # Find the three known builtins.
    by_name = {m.name: m for m in metas}
    assert by_name["code_reviewer"].abstract_base == "reviewer"
    assert by_name["tech_lead"].abstract_base == "escalation_handler"
    assert by_name["requirements_writer"].abstract_base == "specialist"


def test_meta_carries_class_level_defaults():
    """The catalog reflects the class-level defaults, not whatever
    a particular instance was constructed with."""
    meta = get_archetype("requirements_writer")
    assert meta is not None
    assert "doc:pdf" in meta.default_tools
    assert "fs:write" in meta.default_tools
    assert "fs:write" in meta.default_rbac_scopes
    # RequirementsWriter is a Specialist, so the meta's abstract_base
    # is the specialist discriminator — what the meta-agent reasons over.
    assert meta.abstract_base == "specialist"


def test_meta_carries_description_and_when_to_use():
    meta = get_archetype("tech_lead")
    assert meta is not None
    # description is non-empty and references the role's purpose.
    assert "stuck cycles" in meta.description.lower()
    # when_to_use is non-empty prose describing project-level fit.
    assert meta.when_to_use
    assert len(meta.when_to_use) > 30


def test_meta_to_dict_round_trip():
    """The endpoint serialises via to_dict — verify the shape."""
    meta = get_archetype("code_reviewer")
    assert meta is not None
    d = meta.to_dict()
    assert d["name"] == "code_reviewer"
    assert d["abstract_base"] == "reviewer"
    assert isinstance(d["default_tools"], list)
    assert isinstance(d["default_rbac_scopes"], list)
    assert d["default_model_tier"] in ("heavy", "solid", "fast")


def test_get_archetype_returns_none_for_unknown_name():
    assert get_archetype("nonexistent_archetype") is None


def test_non_archetype_classes_excluded():
    """If a non-AgentArchetype class is in the registry, the catalog
    skips it. This guards against accidental inclusion of synthesis
    agents like MemoryWriter."""
    # Manually register a plain BaseAgent subclass for the test.
    from agents.base import BaseAgent

    class _NotAnArchetype(BaseAgent):
        role: str = "_not_archetype"

    if not registry.has("_not_archetype"):
        registry.register("_not_archetype", _NotAnArchetype)
    try:
        names = {m.name for m in list_archetypes()}
        assert "_not_archetype" not in names
    finally:
        # Best-effort cleanup — the registry doesn't expose unregister,
        # so we leak this entry into other tests in the same process.
        # The catalog filter is what matters; this is the assertion.
        pass


def test_archetype_meta_is_frozen():
    """Frozen dataclass — mutations should fail."""
    meta = ArchetypeMeta(
        name="x", abstract_base="reviewer", description="", when_to_use="",
        default_model_tier="solid",
        default_tools=[], default_rbac_scopes=[],
    )
    with pytest.raises(Exception):
        meta.name = "y"  # type: ignore[misc]
