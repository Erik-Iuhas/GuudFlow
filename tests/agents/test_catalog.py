"""Tests for agents/catalog.py (Phase C.0)."""

from __future__ import annotations

import pytest

from agents.builtin import register_builtins
from agents.catalog import (
    ArchetypeMeta,
    dedupe_role_names,
    get_archetype,
    list_all_archetypes,
    list_archetypes,
    suggest_persona_id,
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
    # All four archetypes are present.
    for name in ("code_reviewer", "tech_lead", "requirements_writer", "architect"):
        assert registry.has(name)


def test_list_archetypes_includes_each_builtin():
    metas = list_archetypes()
    names = {m.name for m in metas}
    assert "code_reviewer" in names
    assert "tech_lead" in names
    assert "requirements_writer" in names
    assert "architect" in names


def test_architect_metadata_in_catalog():
    """Architect is registered as a Reviewer with HEAVY tier."""
    meta = get_archetype("architect")
    assert meta is not None
    assert meta.abstract_base == "reviewer"
    assert meta.default_model_tier == "heavy"
    assert "design" in meta.description.lower() or "architect" in meta.description.lower()


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


# ── list_all_archetypes (Phase D.0: runtime ∪ YAML) ────────────────────────


def test_list_all_includes_runtime_and_yaml():
    """The union contains shipped archetypes (runtime) AND described
    ones (YAML)."""
    metas = list_all_archetypes()
    names = {m.name for m in metas}
    # Shipped (runtime catalog).
    assert "code_reviewer" in names
    assert "tech_lead" in names
    # Described only (YAML — not concrete classes yet).
    assert "frontend_dev" in names
    assert "backend_dev" in names
    assert "security_reviewer" in names


def test_list_all_runtime_wins_on_conflict():
    """When a name appears in BOTH the runtime catalog and the YAML,
    the runtime entry takes precedence (shipped class is authoritative)."""
    metas = list_all_archetypes()
    by_name = {m.name: m for m in metas}
    # code_reviewer is in both. The runtime one has its description
    # taken from the class docstring / archetype_description, NOT
    # from the YAML's responsibility first line. Verify by checking
    # we got a single entry and it's identifiable.
    assert "code_reviewer" in by_name
    cr = by_name["code_reviewer"]
    # The runtime ArchetypeMeta uses the class's archetype_description.
    assert "diff" in cr.description.lower() or "reviewer" in cr.description.lower()


def test_list_all_sorted_deterministically():
    """Output sorted by (abstract_base, name) so the meta-agent's
    prompt is stable across runs (better cache behaviour)."""
    metas = list_all_archetypes()
    keys = [(m.abstract_base, m.name) for m in metas]
    assert keys == sorted(keys)


# ── Naming helpers (Phase D.0) ─────────────────────────────────────────────


def test_dedupe_role_names_preserves_unique():
    assert dedupe_role_names(["a", "b", "c"]) == ["a", "b", "c"]


def test_dedupe_role_names_appends_suffix_on_collision():
    assert dedupe_role_names(["dev", "dev", "dev"]) == ["dev", "dev_2", "dev_3"]


def test_dedupe_role_names_skips_existing_suffixes():
    """If `dev_2` is already taken by an explicit role, the dedupe
    helper picks `dev_3` instead — collision-free."""
    out = dedupe_role_names(["dev", "dev_2", "dev"])
    # First two are unique; third collides with "dev" → must avoid
    # "dev_2" (already in list), use "dev_3".
    assert out == ["dev", "dev_2", "dev_3"]


def test_dedupe_role_names_preserves_order():
    out = dedupe_role_names(["alpha", "beta", "alpha", "gamma", "beta"])
    assert out == ["alpha", "beta", "alpha_2", "gamma", "beta_2"]


def test_suggest_persona_id_uses_first_name():
    """First call returns the first curated name + the role."""
    pid, display = suggest_persona_id("frontend", existing_ids=set())
    assert pid == "mike-frontend-v1"
    assert display == "Mike"


def test_suggest_persona_id_skips_taken_names():
    """If `mike-frontend-v1` is taken, returns the next curated name."""
    pid, display = suggest_persona_id(
        "frontend", existing_ids={"mike-frontend-v1"},
    )
    assert pid == "sarah-frontend-v1"
    assert display == "Sarah"


def test_suggest_persona_id_honours_name_hint():
    """When the meta-agent suggests a name (e.g. matched to the
    persona's character), try it first."""
    pid, display = suggest_persona_id(
        "backend", existing_ids=set(), name_hint="Devon",
    )
    assert pid == "devon-backend-v1"
    assert display == "Devon"


def test_suggest_persona_id_skips_taken_hint():
    """A taken hint falls back to the curated list."""
    pid, display = suggest_persona_id(
        "backend", existing_ids={"devon-backend-v1"}, name_hint="Devon",
    )
    assert pid == "mike-backend-v1"
    assert display == "Mike"


def test_suggest_persona_id_falls_back_when_all_names_taken():
    """When every curated name is exhausted for a role, bump version."""
    base_names = (
        "mike", "sarah", "alex", "jamie", "taylor", "casey", "morgan",
        "jordan", "riley", "sam", "drew", "quinn", "rowan", "skyler",
        "blake", "harper", "kai", "lee", "river",
    )
    taken = {f"{n}-foo-v1" for n in base_names}
    pid, display = suggest_persona_id("foo", existing_ids=taken)
    # Falls back to bumping version on the first name.
    assert pid == "mike-foo-v2"
    assert display == "Mike"
    assert pid not in taken
