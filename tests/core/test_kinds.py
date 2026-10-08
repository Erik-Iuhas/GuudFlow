"""Tests for core.kinds: project kinds, their phases and the team they start from."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.contracts import load_library
from core.kinds import (
    KindError,
    ProjectState,
    item_phase,
    kind_of,
    load_kinds,
    parse_kind,
    project_phases,
    render_for_pm,
    team_template,
)


def test_library_loads_and_covers_the_first_release() -> None:
    kinds = load_kinds()
    assert set(kinds) == {"software_app", "library", "game", "maintenance", "research_report",
                          "docs"}
    assert kinds["software_app"].lead_contract == "tech_lead"
    assert kinds["research_report"].lead_contract == "editor"
    assert kinds["maintenance"].lead_contract is None          # no design phase
    assert "adopt" in kinds["game"].start_modes
    assert "screenshots" in kinds["game"].uses and "sandbox" not in kinds["research_report"].uses
    # Every contract a kind names is meant for that kind (or for any kind).
    lib = load_library()
    for kind in kinds.values():
        for cid in kind.team:
            assert not lib[cid].kinds or kind.id in lib[cid].kinds, (kind.id, cid)


def test_parse_rejects_unknown_things() -> None:
    base = {"id": "k", "title": "K", "purpose": "A kind.", "team": ["builder"],
            "start_modes": ["spec_first"],
            "phases": [{"id": "build", "owner": "builder", "gate": "done", "produces": ["code"]}]}
    contracts = list(load_library())
    assert parse_kind(base, contracts).default_start_mode == "spec_first"
    bad = [
        {**base, "team": ["wizard"]},
        {**base, "start_modes": ["yolo"]},
        {**base, "phases": [{"id": "x", "owner": "nobody", "gate": "g"}]},
        {**base, "phases": [{"id": "x", "owner": "owner", "gate": "g", "produces": ["gold"]}]},
        {**base, "uses": ["internet"]},
        {**base, "default_start_mode": "maintain"},
        {**base, "phases": []},
    ]
    for data in bad:
        with pytest.raises(KindError):
            parse_kind(data, contracts)


def test_kind_of_defaults_to_software_app() -> None:
    assert kind_of({}).id == "software_app"
    assert kind_of({"kind": "nope"}).id == "software_app"
    assert kind_of({"kind": "docs"}).id == "docs"


def test_team_template_skips_the_pm_and_numbers_repeats() -> None:
    rows = team_template(load_kinds()["software_app"])
    assert [r["role"] for r in rows] == ["tech_lead", "builder", "builder_2", "tester", "reviewer"]
    lead = rows[0]
    assert lead["role_type"] == "architect" and lead["contract"] == "tech_lead"


def test_pm_paragraph_names_missing_roles() -> None:
    text = render_for_pm(load_kinds()["game"], team_contracts=["builder", "reviewer"])
    assert "Project kind: game" in text and "Design (design), owner tech_lead" in text
    assert "lacks: tech_lead, builder, tester" in text


def _states(strip: object) -> dict[str, str]:
    return {p.id: p.state for p in strip.phases}  # type: ignore[attr-defined]


def test_phases_follow_the_project() -> None:
    kind = load_kinds()["software_app"]
    fresh = project_phases(kind, ProjectState())
    assert fresh.current == "discover"
    approved = project_phases(kind, ProjectState(spec_version=1, has_lead=True))
    assert approved.current == "design"
    no_lead = project_phases(kind, ProjectState(spec_version=1))
    assert _states(no_lead)["design"] == "skipped" and no_lead.current == "build"
    running = project_phases(kind, ProjectState(spec_version=2, has_lead=True,
                                                design_empty=False, running=True))
    assert running.current == "build" and _states(running)["design"] == "done"
    pending = project_phases(kind, ProjectState(spec_version=2, has_lead=True,
                                                design_empty=False, pending=True, accepted=1))
    assert pending.current == "accept"
    later = project_phases(kind, ProjectState(spec_version=2, has_lead=True,
                                              design_empty=False, accepted=3))
    assert later.current == "build" and _states(later)["learn"] == "done"


def test_adopt_starts_at_design_recovery() -> None:
    kind = load_kinds()["game"]
    strip = project_phases(kind, ProjectState(start_mode="adopt", has_lead=True, accepted=3))
    assert _states(strip)["discover"] == "skipped"
    design = next(p for p in strip.phases if p.id == "design")
    assert strip.current == "design" and "recovers the design" in design.note
    waiting = project_phases(kind, ProjectState(start_mode="adopt", has_lead=True,
                                                open_design_drafts=1))
    assert "wait for the Lead seat" in next(p for p in waiting.phases if p.id == "design").note


def test_item_phase() -> None:
    kind = load_kinds()["software_app"]
    early = project_phases(kind, ProjectState(spec_version=1, has_lead=True))
    assert item_phase({"status": "open"}, early) == "design"
    built = project_phases(kind, ProjectState(spec_version=1, has_lead=True, design_empty=False,
                                              accepted=1))
    assert item_phase({"status": "open"}, built) == "ready"
    assert item_phase({"status": "in_run"}, built) == "build"
    assert item_phase({"status": "in_run", "pending": "done"}, built) == "accept"
    assert item_phase({"status": "done"}, built) == "done"


def test_kind_file_must_match_its_name(tmp_path: Path) -> None:
    (tmp_path / "other.yaml").write_text(
        "id: mine\ntitle: T\npurpose: P.\nteam: [builder]\nstart_modes: [spec_first]\n"
        "phases:\n  - {id: build, owner: builder, gate: g}\n")
    with pytest.raises(KindError):
        load_kinds(tmp_path)
