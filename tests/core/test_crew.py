"""Tests for core.crew: every worker has a name."""

from __future__ import annotations

from core.crew import NAME_POOL, crew_names, label, resolve_recipient


def test_names_are_stable_unique_and_respect_choices() -> None:
    roles = ["planner", "builder", "reviewer", "overseer"]
    first = crew_names("pacrogue", roles)
    assert first == crew_names("pacrogue", roles)              # same seat, same person
    assert len(set(first.values())) == len(roles)
    assert all(n in NAME_POOL for n in first.values())
    assert crew_names("other-project", roles) != first          # another project, another crew

    chosen = crew_names("pacrogue", roles, explicit={"builder": "Sarah"},
                        persona_names={"planner": "Grace", "builder": "Ignored"})
    assert chosen["builder"] == "Sarah" and chosen["planner"] == "Grace"
    # A picked name never repeats a chosen one.
    assert len({n.lower() for n in chosen.values()}) == len(roles)


def test_adding_a_role_keeps_everyone_elses_name() -> None:
    team = crew_names("p", ["planner", "builder"])
    bigger = crew_names("p", ["planner", "builder", "overseer"], explicit=team)
    assert {r: bigger[r] for r in team} == team


def test_labels_and_addressing_by_name_or_role() -> None:
    names = {"builder": "Maya", "reviewer": "Ivy"}
    assert label("Maya", "builder") == "Maya (builder)"
    assert label("builder", "builder") == "builder"
    assert resolve_recipient("Ivy", names) == "reviewer"
    assert resolve_recipient("ivy", names) == "reviewer"
    assert resolve_recipient("reviewer", names) == "reviewer"
    assert resolve_recipient("Maya (builder)", names) == "builder"
    assert resolve_recipient("Bob", names) is None
