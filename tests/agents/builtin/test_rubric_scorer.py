"""Tests for agents/builtin/rubric_scorer.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.builtin.rubric_scorer import RubricScorer
from core.file_workspace import FileWorkspace
from core.workspace import Workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    fw = FileWorkspace(tmp_path / "ws")
    workspace = Workspace(run_id="r1", goal="x", files=fw)
    return workspace


async def test_files_present_check_passes(ws: Workspace):
    await ws.files.write("src/auth.py", "x")  # type: ignore[union-attr]
    await ws.files.write("tests/test_auth.py", "x")  # type: ignore[union-attr]
    scenario = {
        "id": "s1",
        "auto_checks": [
            {"name": "files_present", "type": "files_present", "weight": 1.0,
             "paths": ["src/auth.py", "tests/test_auth.py"]},
        ],
    }
    result = await RubricScorer().score(ws, scenario)
    assert result.score == 1.0
    assert result.checks[0].passed is True


async def test_files_present_check_fails(ws: Workspace):
    await ws.files.write("src/auth.py", "x")  # type: ignore[union-attr]
    scenario = {
        "id": "s1",
        "auto_checks": [
            {"name": "fp", "type": "files_present", "weight": 1.0,
             "paths": ["src/auth.py", "missing.py"]},
        ],
    }
    result = await RubricScorer().score(ws, scenario)
    assert result.score == 0.0
    assert "missing" in result.checks[0].detail


async def test_text_match_check(ws: Workspace):
    await ws.files.write("src/auth.py", "class AuthManager:\n    def login(self):\n        pass\n")  # type: ignore[union-attr]
    scenario = {
        "id": "s1",
        "auto_checks": [
            {"name": "has_login", "type": "text_match", "weight": 0.5,
             "path": "src/auth.py", "pattern": r"def\s+login"},
            {"name": "has_logout", "type": "text_match", "weight": 0.5,
             "path": "src/auth.py", "pattern": r"def\s+logout"},
        ],
    }
    result = await RubricScorer().score(ws, scenario)
    # has_login passes (weight 0.5), has_logout fails (weight 0.5) → 0.5
    assert result.score == 0.5


async def test_unsupported_check_type(ws: Workspace):
    scenario = {
        "id": "s1",
        "auto_checks": [
            {"name": "weird", "type": "unknown_kind", "weight": 1.0},
        ],
    }
    result = await RubricScorer().score(ws, scenario)
    assert result.score == 0.0
    assert "unsupported" in result.checks[0].detail


async def test_zero_weight_returns_zero(ws: Workspace):
    scenario = {"id": "s1", "auto_checks": []}
    result = await RubricScorer().score(ws, scenario)
    assert result.score == 0.0
