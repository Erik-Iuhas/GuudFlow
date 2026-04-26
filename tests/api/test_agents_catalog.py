"""Tests for /api/agents/catalog (Phase C.0)."""

from __future__ import annotations

import pytest

from agents.builtin import register_builtins


@pytest.fixture(autouse=True)
def _ensure_builtins():
    register_builtins()


async def test_get_catalog_returns_list_of_archetypes(client):
    r = await client.get("/api/agents/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "archetypes" in body
    names = {a["name"] for a in body["archetypes"]}
    assert "code_reviewer" in names
    assert "tech_lead" in names
    assert "requirements_writer" in names


async def test_catalog_entries_have_metadata(client):
    r = await client.get("/api/agents/catalog")
    body = r.json()
    by_name = {a["name"]: a for a in body["archetypes"]}
    rw = by_name["requirements_writer"]
    assert rw["abstract_base"] == "specialist"
    assert rw["default_model_tier"] in ("heavy", "solid", "fast")
    assert "doc:pdf" in rw["default_tools"]
    assert "fs:write" in rw["default_rbac_scopes"]
    assert rw["description"]
    assert rw["when_to_use"]


async def test_catalog_endpoint_does_not_require_auth(client):
    """Catalog is purely informational. No auth header required."""
    r = await client.get("/api/agents/catalog")
    assert r.status_code == 200


async def test_catalog_lookup_by_name(client):
    r = await client.get("/api/agents/catalog/tech_lead")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "tech_lead"
    assert body["abstract_base"] == "escalation_handler"
    assert body["default_model_tier"] == "heavy"


async def test_catalog_lookup_unknown_returns_404(client):
    r = await client.get("/api/agents/catalog/nonexistent")
    assert r.status_code == 404


# ── Phase F.2 — catalog status ─────────────────────────────────────────────


import json
from pathlib import Path


def _write_scenario(
    data_root: Path,
    scenario_id: str,
    expected_role_types: list[str],
) -> None:
    d = data_root / "training_camp" / "scenarios" / scenario_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenario.json").write_text(json.dumps({
        "scenario_id": scenario_id,
        "goal": "test",
        "project_type": "test",
        "expected_role_types": expected_role_types,
    }), encoding="utf-8")
    (d / "rubric.json").write_text(json.dumps({
        "auto_checks": [{"name": "x", "type": "files_present",
                         "weight": 1.0, "paths": ["a"]}],
    }), encoding="utf-8")


async def test_catalog_status_marks_shipped_archetypes(client):
    """Runtime-registered archetypes (concrete classes) should be
    shipped: true; YAML-only entries should be shipped: false."""
    r = await client.get("/api/agents/catalog/status")
    assert r.status_code == 200
    body = r.json()
    by_name = {a["name"]: a for a in body["archetypes"]}

    assert by_name["code_reviewer"]["shipped"] is True
    assert by_name["tech_lead"]["shipped"] is True
    assert body["n_total"] >= body["n_shipped"]
    assert body["n_described_only"] == body["n_total"] - body["n_shipped"]


async def test_catalog_status_counts_scenarios_per_archetype(
    client, data_root: Path,
):
    """Scenarios with expected_role_types matching catalog names
    contribute to those archetypes' n_scenarios. Scenarios using
    generic role names (e.g. 'developer') don't match any archetype
    and contribute zero — that's an honest signal that authors
    should align scenarios with catalog role types when they want
    coverage to register."""
    _write_scenario(data_root, "s_a", ["frontend_dev", "code_reviewer"])
    _write_scenario(data_root, "s_b", ["backend_dev"])
    _write_scenario(data_root, "s_c", ["frontend_dev", "accessibility_reviewer"])

    r = await client.get("/api/agents/catalog/status")
    body = r.json()
    by_name = {a["name"]: a for a in body["archetypes"]}

    assert by_name["code_reviewer"]["n_scenarios"] == 1  # s_a only
    assert by_name["frontend_dev"]["n_scenarios"] == 2   # s_a + s_c
    assert by_name["backend_dev"]["n_scenarios"] == 1    # s_b
    assert by_name["accessibility_reviewer"]["n_scenarios"] == 1  # s_c

    assert body["n_scenarios"] == 3


async def test_catalog_status_sorted_shipped_first(client):
    r = await client.get("/api/agents/catalog/status")
    body = r.json()
    archetypes = body["archetypes"]
    seen_described = False
    for a in archetypes:
        if not a["shipped"]:
            seen_described = True
        elif seen_described:
            pytest.fail(
                "shipped archetype appeared after a described-only — "
                "ordering should put shipped first"
            )
