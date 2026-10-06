"""Applying approved team ops to a project's agents (plan §10.2)."""

from __future__ import annotations

from typing import Any

import pytest

from core.team_changes import TeamChangeError, apply_team_changes

R = "scope now includes a data pipeline"
AGENTS: list[dict[str, Any]] = [
    {"role": "builder", "role_type": "builder", "depends_on": []},
    {"role": "helper", "role_type": "tester", "depends_on": ["builder"]},
    {"role": "qa", "role_type": "reviewer", "depends_on": ["helper"],
     "review_of": ["helper", "builder"]},
]


def test_remove_drops_the_role_from_others() -> None:
    team, summary = apply_team_changes(
        AGENTS, [{"op": "remove_role", "role": "helper", "reason": R}])
    assert [a["role"] for a in team] == ["builder", "qa"]
    assert team[1]["depends_on"] == [] and team[1]["review_of"] == ["builder"]
    assert summary == {"added": [], "edited": [], "removed": ["helper"]}
    assert AGENTS[2]["review_of"] == ["helper", "builder"]       # the input is not changed


def test_add_fills_defaults_and_edit_sets_only_given_fields() -> None:
    team, summary = apply_team_changes(AGENTS, [
        {"op": "add_role", "role": "rev2", "role_type": "reviewer", "description": "Second look",
         "model_tier": "heavy", "depends_on": ["builder"], "review_of": ["builder"],
         "reason": R},
        {"op": "edit_role", "role": "builder", "model_tier": "heavy", "reason": R}])
    new = team[-1]
    assert new["is_reviewer"] is True and new["max_review_rounds"] == 2
    assert new["capabilities"] == {} and "name" not in new
    assert team[0]["model_tier"] == "heavy" and team[0]["role_type"] == "builder"
    assert summary == {"added": ["rev2"], "edited": ["builder"], "removed": []}


def test_ops_that_do_not_fit_raise() -> None:
    with pytest.raises(TeamChangeError):
        apply_team_changes(AGENTS, [{"op": "remove_role", "role": "ghost", "reason": R}])
    with pytest.raises(TeamChangeError):
        apply_team_changes(AGENTS, [{"op": "add_role", "role": "qa", "role_type": "reviewer",
                                     "model_tier": "fast", "reason": R}])
