"""Tests for agents/builtin/rubric_scorer.py — Phase F.1.1.

Covers the post-run on_run_complete hook: load scenario from disk,
evaluate auto_checks against the run's working/, post a single
RUBRIC_SCORE signal with the weighted score + per-check detail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agents.builtin.rubric_scorer import RubricScorer
from core.file_workspace import FileWorkspace
from core.signals import SignalType
from core.workspace import Workspace


# ── Helpers ────────────────────────────────────────────────────────────────


@dataclass
class _StubProject:
    """Minimal project shape — RubricScorer only reads
    `training_scenario_id`."""

    training_scenario_id: str | None = None


def _make_workspace(tmp_path: Path) -> Workspace:
    fw = FileWorkspace(tmp_path / "ws")
    return Workspace(run_id="r_test", goal="x", files=fw)


def _write_scenario(
    scenarios_root: Path, scenario_id: str, *, rubric_payload: dict[str, Any],
) -> Path:
    d = scenarios_root / scenario_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenario.json").write_text(json.dumps({
        "scenario_id": scenario_id,
        "goal": "test",
        "project_type": "test",
        "expected_role_types": ["developer"],
    }), encoding="utf-8")
    (d / "rubric.json").write_text(json.dumps(rubric_payload), encoding="utf-8")
    return d


def _rubric_signal_detail(workspace: Workspace) -> dict[str, Any]:
    """Pull the single RUBRIC_SCORE signal off the board and parse its
    detail JSON. Asserts exactly one such signal was posted."""
    rubric_signals = [
        s for s in workspace.board if s.type is SignalType.RUBRIC_SCORE
    ]
    assert len(rubric_signals) == 1, (
        f"expected exactly one RUBRIC_SCORE, got {len(rubric_signals)}"
    )
    return json.loads(rubric_signals[0].detail)


# ── No-op when not in training mode ────────────────────────────────────────


async def test_no_signal_when_no_scenario_id(tmp_path: Path):
    """A regular production run (no training_scenario_id) gets no
    RUBRIC_SCORE on the board."""
    ws = _make_workspace(tmp_path)
    project = _StubProject(training_scenario_id=None)

    await RubricScorer().on_run_complete(
        workspace=ws, project=project, run_id="r1",
        run_root=tmp_path, scenarios_root=tmp_path / "scenarios",
    )

    assert all(s.type is not SignalType.RUBRIC_SCORE for s in ws.board)


# ── files_present ──────────────────────────────────────────────────────────


async def test_files_present_full_pass(tmp_path: Path):
    ws = _make_workspace(tmp_path)
    await ws.files.write("src/auth.py", "x")
    await ws.files.write("tests/test_auth.py", "x")

    scenarios_root = tmp_path / "scenarios"
    _write_scenario(scenarios_root, "s1", rubric_payload={
        "auto_checks": [
            {"name": "fp", "type": "files_present", "weight": 1.0,
             "paths": ["src/auth.py", "tests/test_auth.py"]},
        ],
    })
    project = _StubProject(training_scenario_id="s1")

    result = await RubricScorer().on_run_complete(
        workspace=ws, project=project, run_id="r1",
        run_root=tmp_path, scenarios_root=scenarios_root,
    )

    assert result.score == 1.0
    detail = _rubric_signal_detail(ws)
    assert detail["score"] == 1.0
    assert detail["scenario_id"] == "s1"
    assert detail["checks"][0]["passed"] is True


async def test_files_present_partial_miss_zero_score(tmp_path: Path):
    """A files_present check is all-or-nothing: any missing file means
    that check fails, which (since it's the only check) makes the
    rubric score zero."""
    ws = _make_workspace(tmp_path)
    await ws.files.write("src/auth.py", "x")

    scenarios_root = tmp_path / "scenarios"
    _write_scenario(scenarios_root, "s1", rubric_payload={
        "auto_checks": [
            {"name": "fp", "type": "files_present", "weight": 1.0,
             "paths": ["src/auth.py", "missing.py"]},
        ],
    })

    result = await RubricScorer().on_run_complete(
        workspace=ws, project=_StubProject(training_scenario_id="s1"),
        run_id="r1", run_root=tmp_path, scenarios_root=scenarios_root,
    )
    assert result.score == 0.0


# ── text_contains ─────────────────────────────────────────────────────────


async def test_text_contains_partial_score(tmp_path: Path):
    """Two text_contains checks at 0.5 each. One matches, one doesn't —
    score should be 0.5 (50% of weight earned)."""
    ws = _make_workspace(tmp_path)
    await ws.files.write("src/auth.py",
                          "class AuthManager:\n    def login(self):\n        pass\n")

    scenarios_root = tmp_path / "scenarios"
    _write_scenario(scenarios_root, "s1", rubric_payload={
        "auto_checks": [
            {"name": "has_login", "type": "text_contains", "weight": 0.5,
             "path": "src/auth.py", "needle": "def login"},
            {"name": "has_logout", "type": "text_contains", "weight": 0.5,
             "path": "src/auth.py", "needle": "def logout"},
        ],
    })
    result = await RubricScorer().on_run_complete(
        workspace=ws, project=_StubProject(training_scenario_id="s1"),
        run_id="r1", run_root=tmp_path, scenarios_root=scenarios_root,
    )

    assert result.score == 0.5


async def test_text_contains_missing_file_fails_check(tmp_path: Path):
    ws = _make_workspace(tmp_path)
    scenarios_root = tmp_path / "scenarios"
    _write_scenario(scenarios_root, "s1", rubric_payload={
        "auto_checks": [
            {"name": "x", "type": "text_contains", "weight": 1.0,
             "path": "ghost.py", "needle": "anything"},
        ],
    })
    result = await RubricScorer().on_run_complete(
        workspace=ws, project=_StubProject(training_scenario_id="s1"),
        run_id="r1", run_root=tmp_path, scenarios_root=scenarios_root,
    )
    assert result.score == 0.0


# ── Scoring failures ───────────────────────────────────────────────────────


async def test_missing_scenario_dir_posts_error_signal(tmp_path: Path):
    """When the scenario doesn't exist on disk, post a RUBRIC_SCORE
    with score=0 and an error in detail — don't raise."""
    ws = _make_workspace(tmp_path)
    result = await RubricScorer().on_run_complete(
        workspace=ws, project=_StubProject(training_scenario_id="ghost"),
        run_id="r1", run_root=tmp_path, scenarios_root=tmp_path / "scenarios",
    )
    assert result.score == 0.0
    detail = _rubric_signal_detail(ws)
    assert detail["score"] == 0.0
    assert "error" in detail


# ── Mixed weights ─────────────────────────────────────────────────────────


async def test_mixed_check_kinds_weighted_correctly(tmp_path: Path):
    """files_present (0.4) passes, text_contains (0.6) passes — score 1.0."""
    ws = _make_workspace(tmp_path)
    await ws.files.write("hello.py", "def greet():\n    return 'hi'\n")

    scenarios_root = tmp_path / "scenarios"
    _write_scenario(scenarios_root, "s1", rubric_payload={
        "auto_checks": [
            {"name": "exists", "type": "files_present", "weight": 0.4,
             "paths": ["hello.py"]},
            {"name": "has_greet", "type": "text_contains", "weight": 0.6,
             "path": "hello.py", "needle": "def greet"},
        ],
    })
    result = await RubricScorer().on_run_complete(
        workspace=ws, project=_StubProject(training_scenario_id="s1"),
        run_id="r1", run_root=tmp_path, scenarios_root=scenarios_root,
    )
    assert result.score == 1.0


async def test_signal_summary_includes_score_and_scenario(tmp_path: Path):
    ws = _make_workspace(tmp_path)
    await ws.files.write("hello.py", "def greet(): pass")

    scenarios_root = tmp_path / "scenarios"
    _write_scenario(scenarios_root, "s_neat", rubric_payload={
        "auto_checks": [
            {"name": "x", "type": "files_present", "weight": 1.0,
             "paths": ["hello.py"]},
        ],
    })
    await RubricScorer().on_run_complete(
        workspace=ws, project=_StubProject(training_scenario_id="s_neat"),
        run_id="r1", run_root=tmp_path, scenarios_root=scenarios_root,
    )
    rubric_signals = [s for s in ws.board if s.type is SignalType.RUBRIC_SCORE]
    summary = rubric_signals[0].summary
    assert "1.00" in summary
    assert "s_neat" in summary
