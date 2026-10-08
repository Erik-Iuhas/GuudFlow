"""Tests for core.contracts: role contracts (role contracts plan §2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.contracts import (
    ContractError,
    contract_id_for,
    contracts_for_team,
    load_for_project,
    load_library,
    parse_contract,
    render_for_brief,
)
from core.harness.brief import BriefInputs, build_brief


def test_library_loads_and_references_resolve() -> None:
    lib = load_library()
    for cid in ("product_manager", "tech_lead", "builder", "tester", "reviewer", "researcher",
                "overseer", "planner", "editor", "writer", "fact_checker", "docs_lead"):
        assert cid in lib, cid
    for c in lib.values():
        for h in c.hands_off:
            assert h.to in lib or h.to == "owner", (c.id, h.to)
        assert c.checked_by in lib or c.checked_by in ("owner", ""), c.id
    assert lib["tech_lead"].may_change("design") and not lib["builder"].may_change("design")
    assert lib["product_manager"].may_change("spec") and not lib["tech_lead"].may_change("spec")


def test_parse_refuses_bad_contracts() -> None:
    with pytest.raises(ContractError, match="needs an id"):
        parse_contract({"title": "x"})
    with pytest.raises(ContractError, match="missing purpose"):
        parse_contract({"id": "x", "title": "X", "role_type": "builder"})
    with pytest.raises(ContractError, match="unknown artifact"):
        parse_contract({"id": "x", "title": "X", "purpose": "p", "role_type": "builder",
                        "owns": ["everything"]})
    with pytest.raises(ContractError, match="hands_off"):
        parse_contract({"id": "x", "title": "X", "purpose": "p", "role_type": "builder",
                        "hands_off": [{"to": "reviewer"}]})


def test_project_override_replaces_fields_and_appends_rules(tmp_path: Path) -> None:
    d = tmp_path / "projects" / "p1" / "contracts"
    d.mkdir(parents=True)
    (d / "builder.yaml").write_text("purpose: Builds the game.\nrules:\n  - Use integer maths.\n")
    (d / "level_designer.yaml").write_text(
        "id: level_designer\ntitle: Level designer\npurpose: Designs levels.\nrole_type: builder\n")
    lib = load_for_project(tmp_path, "p1")
    b = lib["builder"]
    assert b.purpose == "Builds the game." and b.source == "project"
    assert b.rules[-1] == "Use integer maths." and len(b.rules) > 1     # library rules kept
    assert b.measures and b.measures[0].key == "review_rounds"
    assert lib["level_designer"].source == "project"


def test_team_rows_pick_named_or_default_contracts() -> None:
    lib = load_library()
    assert contract_id_for({"contract": "tech_lead"}, "architect") == "tech_lead"
    assert contract_id_for({}, "frontend_builder") == "builder"
    team = contracts_for_team([("lead", "architect", "tech_lead"), ("sol", "builder", None),
                               ("x", "builder", "nonexistent")], lib)
    assert team["lead"].id == "tech_lead" and team["sol"].id == "builder" and "x" not in team


def test_brief_section_names_teammates_and_rules() -> None:
    lib = load_library()
    text = render_for_brief(lib["builder"], {"reviewer": ["rev"], "builder": ["logic"]},
                            {"rev": "Ines", "logic": "Sol"})
    assert "**Builder**" in text and "to Sol:" in text and "checked by Ines" in text
    assert "Export only what the design declares" in text
    brief = build_brief(BriefInputs(goal="g", role="logic", description="d", contract=text))
    assert "## Your role contract" in brief
    assert brief.index("## Your job") < brief.index("## Your role contract")
    cont = build_brief(BriefInputs(goal="g", role="logic", description="d", contract=text,
                                   continuation=True))
    assert "Your role contract" not in cont
