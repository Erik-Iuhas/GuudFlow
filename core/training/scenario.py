"""Scenario loader — DESIGN §35.

A training scenario lives at `data/training_camp/scenarios/{name}/`
and is composed of:

    scenario.json      goal + project type + expected role types
    rubric.json        weighted auto-checks (and optional manual checks)
    inputs/            spec / sample data / mockups copied into runs
    reference_outputs/ what a strong solution looks like (optional)
    README.md          human-facing description (optional)

This module reads those files into typed `Scenario` / `Rubric` objects.
Strict validation: a malformed scenario raises `ScenarioError` rather
than silently producing a partial run config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ScenarioError(ValueError):
    """A scenario directory is missing required files or is malformed."""


# Supported auto-check kinds (Phase F.1 keeps this list small on purpose;
# pytest/ruff need sandboxed subprocess execution, deferred to F.1.5+).
SUPPORTED_AUTO_CHECK_KINDS = frozenset({"files_present", "text_contains"})


@dataclass(frozen=True)
class AutoCheck:
    """One machine-checkable rubric criterion. The `kind` field is the
    discriminator; specific kinds carry their own params (paths, needle,
    etc.) in the `params` dict."""

    name: str
    kind: str
    weight: float
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManualCheck:
    """Forward-compat — F.1 ships the schema but doesn't surface manual
    checks in the UI."""

    name: str
    weight: float
    prompt: str


@dataclass(frozen=True)
class Rubric:
    auto_checks: tuple[AutoCheck, ...]
    manual_checks: tuple[ManualCheck, ...] = ()

    @property
    def total_auto_weight(self) -> float:
        return sum(c.weight for c in self.auto_checks)


@dataclass(frozen=True)
class Scenario:
    """One scenario from `data/training_camp/scenarios/{scenario_id}/`."""

    scenario_id: str
    goal: str
    project_type: str
    expected_role_types: tuple[str, ...]
    rubric: Rubric
    scenario_dir: Path
    inputs_dir: Path | None = None
    reference_outputs_dir: Path | None = None
    description: str = ""


# ── Loader ─────────────────────────────────────────────────────────────────


def list_scenarios(root: Path) -> list[Scenario]:
    """Discover and load every scenario under `root`.

    Walks one level deep — subdirectories of `root` whose name doesn't
    start with `_` and that contain a `scenario.json`. Sorted by id so
    `GET /api/training/scenarios` returns a stable order."""
    if not root.is_dir():
        return []
    out: list[Scenario] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if not (child / "scenario.json").is_file():
            continue
        try:
            out.append(load_scenario(child))
        except ScenarioError:
            # A malformed scenario shouldn't break listing; skip it.
            continue
    return out


def load_scenario(scenario_dir: Path) -> Scenario:
    """Load a single scenario from its directory. Raises `ScenarioError`
    on missing or malformed files."""
    if not scenario_dir.is_dir():
        raise ScenarioError(f"scenario directory does not exist: {scenario_dir}")

    scenario_path = scenario_dir / "scenario.json"
    if not scenario_path.is_file():
        raise ScenarioError(f"missing scenario.json in {scenario_dir}")
    try:
        scenario_payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ScenarioError(f"scenario.json is not valid JSON: {e}") from e

    rubric_path = scenario_dir / "rubric.json"
    if not rubric_path.is_file():
        raise ScenarioError(f"missing rubric.json in {scenario_dir}")
    try:
        rubric_payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ScenarioError(f"rubric.json is not valid JSON: {e}") from e

    scenario_id = str(scenario_payload.get("scenario_id") or scenario_dir.name)
    goal = scenario_payload.get("goal")
    if not goal or not isinstance(goal, str):
        raise ScenarioError(
            f"scenario {scenario_id!r}: 'goal' must be a non-empty string"
        )
    project_type = str(scenario_payload.get("project_type") or "")
    role_types = scenario_payload.get("expected_role_types") or []
    if not isinstance(role_types, list):
        raise ScenarioError(
            f"scenario {scenario_id!r}: 'expected_role_types' must be a list"
        )

    rubric = _parse_rubric(scenario_id, rubric_payload)

    inputs_dir = scenario_dir / "inputs"
    reference_dir = scenario_dir / "reference_outputs"
    readme = scenario_dir / "README.md"
    description = readme.read_text(encoding="utf-8") if readme.is_file() else ""

    return Scenario(
        scenario_id=scenario_id,
        goal=goal,
        project_type=project_type,
        expected_role_types=tuple(str(r) for r in role_types),
        rubric=rubric,
        scenario_dir=scenario_dir,
        inputs_dir=inputs_dir if inputs_dir.is_dir() else None,
        reference_outputs_dir=reference_dir if reference_dir.is_dir() else None,
        description=description,
    )


def _parse_rubric(scenario_id: str, payload: dict[str, Any]) -> Rubric:
    raw_auto = payload.get("auto_checks")
    if not isinstance(raw_auto, list) or not raw_auto:
        raise ScenarioError(
            f"scenario {scenario_id!r}: rubric.auto_checks must be a non-empty list"
        )

    autos: list[AutoCheck] = []
    for i, entry in enumerate(raw_auto):
        if not isinstance(entry, dict):
            raise ScenarioError(
                f"scenario {scenario_id!r}: auto_checks[{i}] must be an object"
            )
        name = entry.get("name")
        kind = entry.get("type") or entry.get("kind")
        weight = entry.get("weight")
        if not isinstance(name, str) or not name:
            raise ScenarioError(
                f"scenario {scenario_id!r}: auto_checks[{i}].name is required"
            )
        if not isinstance(kind, str) or kind not in SUPPORTED_AUTO_CHECK_KINDS:
            raise ScenarioError(
                f"scenario {scenario_id!r}: auto_checks[{i}].type {kind!r} not in "
                f"{sorted(SUPPORTED_AUTO_CHECK_KINDS)}"
            )
        if not isinstance(weight, (int, float)) or weight <= 0:
            raise ScenarioError(
                f"scenario {scenario_id!r}: auto_checks[{i}].weight must be > 0"
            )
        params = {k: v for k, v in entry.items() if k not in ("name", "type", "kind", "weight")}
        autos.append(AutoCheck(
            name=name, kind=kind, weight=float(weight), params=params,
        ))

    raw_manual = payload.get("manual_checks") or []
    if not isinstance(raw_manual, list):
        raise ScenarioError(
            f"scenario {scenario_id!r}: rubric.manual_checks must be a list"
        )
    manuals: list[ManualCheck] = []
    for i, entry in enumerate(raw_manual):
        if not isinstance(entry, dict):
            raise ScenarioError(
                f"scenario {scenario_id!r}: manual_checks[{i}] must be an object"
            )
        manuals.append(ManualCheck(
            name=str(entry.get("name", f"manual_{i}")),
            weight=float(entry.get("weight", 0.0)),
            prompt=str(entry.get("prompt", "")),
        ))

    return Rubric(auto_checks=tuple(autos), manual_checks=tuple(manuals))
