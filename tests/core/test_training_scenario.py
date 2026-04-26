"""Tests for core/training/scenario.py — scenario loader (Phase F.1.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.training.scenario import (
    ScenarioError,
    list_scenarios,
    load_scenario,
)


def _write_scenario(
    root: Path, scenario_id: str, *, scenario: dict, rubric: dict,
) -> Path:
    d = root / scenario_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    (d / "rubric.json").write_text(json.dumps(rubric), encoding="utf-8")
    return d


_VALID_SCENARIO = {
    "scenario_id": "hello_v1",
    "goal": "Write hello.py",
    "project_type": "python_module",
    "expected_role_types": ["developer", "reviewer"],
}

_VALID_RUBRIC = {
    "auto_checks": [
        {"name": "exists", "type": "files_present", "weight": 1.0, "paths": ["hello.py"]},
    ],
}


def test_loads_minimal_scenario(tmp_path: Path):
    d = _write_scenario(tmp_path, "hello_v1",
                        scenario=_VALID_SCENARIO, rubric=_VALID_RUBRIC)
    s = load_scenario(d)

    assert s.scenario_id == "hello_v1"
    assert s.goal == "Write hello.py"
    assert s.project_type == "python_module"
    assert s.expected_role_types == ("developer", "reviewer")
    assert len(s.rubric.auto_checks) == 1
    check = s.rubric.auto_checks[0]
    assert check.name == "exists"
    assert check.kind == "files_present"
    assert check.weight == 1.0
    assert check.params == {"paths": ["hello.py"]}


def test_loads_text_contains_check(tmp_path: Path):
    d = _write_scenario(tmp_path, "with_text",
                        scenario=_VALID_SCENARIO,
                        rubric={"auto_checks": [
                            {"name": "has_def", "type": "text_contains",
                             "weight": 0.5, "path": "hello.py", "needle": "def greet"},
                        ]})
    s = load_scenario(d)
    assert s.rubric.auto_checks[0].kind == "text_contains"
    assert s.rubric.auto_checks[0].params["needle"] == "def greet"


def test_total_auto_weight(tmp_path: Path):
    d = _write_scenario(tmp_path, "weighted",
                        scenario=_VALID_SCENARIO,
                        rubric={"auto_checks": [
                            {"name": "a", "type": "files_present", "weight": 0.3, "paths": ["a"]},
                            {"name": "b", "type": "files_present", "weight": 0.7, "paths": ["b"]},
                        ]})
    s = load_scenario(d)
    assert s.rubric.total_auto_weight == 1.0


def test_loads_manual_checks_for_forward_compat(tmp_path: Path):
    d = _write_scenario(tmp_path, "manual",
                        scenario=_VALID_SCENARIO,
                        rubric={
                            "auto_checks": [
                                {"name": "a", "type": "files_present", "weight": 0.5, "paths": ["a"]},
                            ],
                            "manual_checks": [
                                {"name": "clarity", "weight": 0.5,
                                 "prompt": "Score 1-5"},
                            ],
                        })
    s = load_scenario(d)
    assert len(s.rubric.manual_checks) == 1
    assert s.rubric.manual_checks[0].prompt == "Score 1-5"


def test_loads_inputs_and_reference_dirs_when_present(tmp_path: Path):
    d = _write_scenario(tmp_path, "with_dirs",
                        scenario=_VALID_SCENARIO, rubric=_VALID_RUBRIC)
    (d / "inputs").mkdir()
    (d / "inputs" / "spec.md").write_text("...", encoding="utf-8")
    (d / "reference_outputs").mkdir()
    s = load_scenario(d)
    assert s.inputs_dir == d / "inputs"
    assert s.reference_outputs_dir == d / "reference_outputs"


def test_skips_inputs_dir_when_not_present(tmp_path: Path):
    d = _write_scenario(tmp_path, "no_inputs",
                        scenario=_VALID_SCENARIO, rubric=_VALID_RUBRIC)
    s = load_scenario(d)
    assert s.inputs_dir is None
    assert s.reference_outputs_dir is None


def test_readme_populates_description(tmp_path: Path):
    d = _write_scenario(tmp_path, "with_readme",
                        scenario=_VALID_SCENARIO, rubric=_VALID_RUBRIC)
    (d / "README.md").write_text("# A scenario", encoding="utf-8")
    s = load_scenario(d)
    assert "A scenario" in s.description


# ── Validation errors ─────────────────────────────────────────────────────


def test_missing_scenario_json_raises(tmp_path: Path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "rubric.json").write_text(json.dumps(_VALID_RUBRIC), encoding="utf-8")
    with pytest.raises(ScenarioError, match="scenario.json"):
        load_scenario(d)


def test_missing_rubric_json_raises(tmp_path: Path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "scenario.json").write_text(json.dumps(_VALID_SCENARIO), encoding="utf-8")
    with pytest.raises(ScenarioError, match="rubric.json"):
        load_scenario(d)


def test_invalid_json_raises(tmp_path: Path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "scenario.json").write_text("not json", encoding="utf-8")
    (d / "rubric.json").write_text(json.dumps(_VALID_RUBRIC), encoding="utf-8")
    with pytest.raises(ScenarioError, match="not valid JSON"):
        load_scenario(d)


def test_missing_goal_raises(tmp_path: Path):
    d = _write_scenario(tmp_path, "no_goal",
                        scenario={**_VALID_SCENARIO, "goal": ""},
                        rubric=_VALID_RUBRIC)
    with pytest.raises(ScenarioError, match="goal"):
        load_scenario(d)


def test_unknown_check_kind_raises(tmp_path: Path):
    d = _write_scenario(tmp_path, "bad_kind",
                        scenario=_VALID_SCENARIO,
                        rubric={"auto_checks": [
                            {"name": "x", "type": "exec_pytest", "weight": 1.0},
                        ]})
    with pytest.raises(ScenarioError, match="exec_pytest"):
        load_scenario(d)


def test_zero_weight_raises(tmp_path: Path):
    d = _write_scenario(tmp_path, "zero",
                        scenario=_VALID_SCENARIO,
                        rubric={"auto_checks": [
                            {"name": "x", "type": "files_present", "weight": 0.0,
                             "paths": ["a"]},
                        ]})
    with pytest.raises(ScenarioError, match="weight"):
        load_scenario(d)


# ── list_scenarios ────────────────────────────────────────────────────────


def test_list_scenarios_returns_sorted(tmp_path: Path):
    _write_scenario(tmp_path, "b_test",
                    scenario={**_VALID_SCENARIO, "scenario_id": "b_test"},
                    rubric=_VALID_RUBRIC)
    _write_scenario(tmp_path, "a_test",
                    scenario={**_VALID_SCENARIO, "scenario_id": "a_test"},
                    rubric=_VALID_RUBRIC)
    items = list_scenarios(tmp_path)
    assert [s.scenario_id for s in items] == ["a_test", "b_test"]


def test_list_scenarios_skips_underscore_prefixed(tmp_path: Path):
    _write_scenario(tmp_path, "real",
                    scenario={**_VALID_SCENARIO, "scenario_id": "real"},
                    rubric=_VALID_RUBRIC)
    _write_scenario(tmp_path, "_internal",
                    scenario={**_VALID_SCENARIO, "scenario_id": "_internal"},
                    rubric=_VALID_RUBRIC)
    items = list_scenarios(tmp_path)
    assert [s.scenario_id for s in items] == ["real"]


def test_list_scenarios_skips_malformed(tmp_path: Path):
    """One scenario broken shouldn't break listing of the rest."""
    _write_scenario(tmp_path, "good",
                    scenario={**_VALID_SCENARIO, "scenario_id": "good"},
                    rubric=_VALID_RUBRIC)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "scenario.json").write_text("not json", encoding="utf-8")
    (bad / "rubric.json").write_text(json.dumps(_VALID_RUBRIC), encoding="utf-8")

    items = list_scenarios(tmp_path)
    assert [s.scenario_id for s in items] == ["good"]


def test_list_scenarios_returns_empty_when_root_missing(tmp_path: Path):
    assert list_scenarios(tmp_path / "does-not-exist") == []


# ── The seed scenario in repo ────────────────────────────────────────────


def test_seed_scenario_loads(tmp_path: Path):
    """The shipped hello_python_v1 seed scenario must load cleanly —
    catches typos in seed data the moment they're introduced."""
    repo_root = Path(__file__).resolve().parents[2]
    seed_dir = repo_root / "data" / "training_camp" / "scenarios" / "hello_python_v1"
    assert seed_dir.is_dir(), f"seed scenario missing at {seed_dir}"

    s = load_scenario(seed_dir)
    assert s.scenario_id == "hello_python_v1"
    assert "greet" in s.goal
    assert s.rubric.total_auto_weight == pytest.approx(1.0)
    # Three checks in the seed rubric.
    assert len(s.rubric.auto_checks) == 3
