"""Loader for example project templates — `data/example_projects/`.

One-click project templates surfaced in the Studio's Projects view. Each
folder under the templates root ships a `template.json` with a UI envelope
(title, category, icon, summary, hints) plus a `project_template` block
that maps onto the `ProjectConfig` schema in `core/runner.py`.

This module reads those files into typed `ExampleProject` objects. Strict
validation: a malformed template raises `ExampleProjectError` rather than
silently producing a broken catalog entry. Listing tolerates malformed
entries (skips them) so one bad template can't break the whole gallery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ExampleProjectError(ValueError):
    """A template directory is missing required files or is malformed."""


VALID_CATEGORIES = frozenset({
    "Coding",
    "Documentation",
    "Refactor",
    "Research",
})


@dataclass(frozen=True)
class ExampleProjectRequires:
    """Hints surfaced to the user before instantiation. Purely advisory —
    the API doesn't block instantiation on any of these."""

    source_repo: bool = False
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExampleProject:
    """One template from `data/example_projects/{slug}/`."""

    id: str
    title: str
    category: str
    icon: str
    summary: str
    details: str
    default_project_id: str
    default_goal: str
    requires: ExampleProjectRequires
    project_template: dict[str, Any]
    template_dir: Path
    seed_inputs_dir: Path | None = None

    def to_summary(self) -> dict[str, Any]:
        """Public shape — what `GET /api/example-projects` returns. Hides
        the on-disk Path objects."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "icon": self.icon,
            "summary": self.summary,
            "details": self.details,
            "default_project_id": self.default_project_id,
            "default_goal": self.default_goal,
            "requires": {
                "source_repo": self.requires.source_repo,
                "hints": list(self.requires.hints),
            },
            "has_seed_inputs": self.seed_inputs_dir is not None,
        }


# ── Loader ─────────────────────────────────────────────────────────────────


def list_example_projects(root: Path) -> list[ExampleProject]:
    """Discover and load every template under `root`.

    Walks one level deep — subdirectories of `root` whose name doesn't
    start with `_` and that contain a `template.json`. Sorted by id so
    `GET /api/example-projects` returns a stable order. Malformed
    entries are silently skipped (logged at WARNING) so one bad
    template can't break the whole gallery.
    """
    if not root.is_dir():
        return []
    out: list[ExampleProject] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue
        if not (child / "template.json").is_file():
            continue
        try:
            out.append(load_example_project(child))
        except ExampleProjectError:
            # A malformed template shouldn't break listing; skip it.
            import logging
            logging.getLogger(__name__).warning(
                "skipping malformed example project at %s", child,
            )
            continue
    return out


def load_example_project(template_dir: Path) -> ExampleProject:
    """Load a single template from its directory. Raises
    `ExampleProjectError` on missing or malformed files."""
    if not template_dir.is_dir():
        raise ExampleProjectError(
            f"template directory does not exist: {template_dir}"
        )

    template_path = template_dir / "template.json"
    if not template_path.is_file():
        raise ExampleProjectError(f"missing template.json in {template_dir}")
    try:
        payload = json.loads(template_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ExampleProjectError(
            f"template.json is not valid JSON: {e}"
        ) from e

    template_id = _str_field(payload, "id", required=True)
    title = _str_field(payload, "title", required=True)
    category = _str_field(payload, "category", required=True)
    if category not in VALID_CATEGORIES:
        raise ExampleProjectError(
            f"template {template_id!r}: category {category!r} not in "
            f"{sorted(VALID_CATEGORIES)}"
        )

    icon = _str_field(payload, "icon", required=False, default="📦")
    summary = _str_field(payload, "summary", required=True)
    details = _str_field(payload, "details", required=False, default="")
    default_project_id = _str_field(payload, "default_project_id", required=True)
    default_goal = _str_field(payload, "default_goal", required=True)

    requires = _parse_requires(template_id, payload.get("requires") or {})

    project_template = payload.get("project_template")
    if not isinstance(project_template, dict):
        raise ExampleProjectError(
            f"template {template_id!r}: 'project_template' must be an object"
        )
    agents = project_template.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ExampleProjectError(
            f"template {template_id!r}: project_template.agents must be a "
            "non-empty list"
        )
    for i, a in enumerate(agents):
        if not isinstance(a, dict) or not a.get("role"):
            raise ExampleProjectError(
                f"template {template_id!r}: agents[{i}] missing 'role'"
            )

    seed_inputs = template_dir / "seed_inputs"
    seed_inputs_dir = seed_inputs if seed_inputs.is_dir() else None

    return ExampleProject(
        id=template_id,
        title=title,
        category=category,
        icon=icon,
        summary=summary,
        details=details,
        default_project_id=default_project_id,
        default_goal=default_goal,
        requires=requires,
        project_template=project_template,
        template_dir=template_dir,
        seed_inputs_dir=seed_inputs_dir,
    )


def find_example_project(root: Path, template_id: str) -> ExampleProject | None:
    """Find one template by id. Returns None if not found. Used by the
    instantiate route — strict load (raises) so a malformed template
    surfaces as a 500 rather than 404."""
    for child in root.iterdir() if root.is_dir() else []:
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if not (child / "template.json").is_file():
            continue
        try:
            payload = json.loads(
                (child / "template.json").read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            continue
        if payload.get("id") == template_id:
            return load_example_project(child)
    return None


def materialize_project_config(
    template: ExampleProject,
    project_id: str,
    goal: str,
) -> dict[str, Any]:
    """Compose the project dict that gets POSTed to `ProjectStore.create`.

    Merges the `project_template` block with the caller-supplied
    project_id and goal. Returns a fresh dict — does not mutate the
    template's stored config."""
    config: dict[str, Any] = json.loads(json.dumps(template.project_template))
    config["project_id"] = project_id
    config["goal"] = goal
    return config


# ── helpers ────────────────────────────────────────────────────────────────


def _str_field(
    payload: dict[str, Any],
    key: str,
    *,
    required: bool,
    default: str = "",
) -> str:
    value = payload.get(key)
    if value is None or value == "":
        if required:
            raise ExampleProjectError(f"missing required field {key!r}")
        return default
    if not isinstance(value, str):
        raise ExampleProjectError(f"field {key!r} must be a string")
    return value


def _parse_requires(
    template_id: str, payload: dict[str, Any],
) -> ExampleProjectRequires:
    if not isinstance(payload, dict):
        raise ExampleProjectError(
            f"template {template_id!r}: 'requires' must be an object"
        )
    source_repo = bool(payload.get("source_repo", False))
    hints_raw = payload.get("hints") or []
    if not isinstance(hints_raw, list):
        raise ExampleProjectError(
            f"template {template_id!r}: requires.hints must be a list"
        )
    hints = tuple(str(h) for h in hints_raw)
    return ExampleProjectRequires(source_repo=source_repo, hints=hints)
