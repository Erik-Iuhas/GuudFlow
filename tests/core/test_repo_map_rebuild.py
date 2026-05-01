"""Track 1d — repo_map cache stability via dirty-flag rebuilds.

The runner used to rebuild repo_map after every GIT_COMMIT signal. With
many commits in a single iteration, that busted the system-prompt cache
once per commit. Now: GIT_COMMIT just sets a dirty flag, and the rebuild
fires once at the next agent-transition (in _drive_agent_run pre-spawn).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.runner import AgentRunner, ProjectConfig
from core.llm import MockLLMClient


def _project() -> ProjectConfig:
    return ProjectConfig(
        project_id="track-1d",
        goal="test",
        agents=[],
        rbac={},
        max_iterations=3,
    )


def test_dirty_flag_default_false(tmp_path: Path):
    runner = AgentRunner(
        project=_project(),
        run_id="r-1d",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    assert runner._repo_map_dirty is False


def test_rebuild_if_dirty_no_op_when_clean(tmp_path: Path):
    runner = AgentRunner(
        project=_project(),
        run_id="r-1d",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    with patch("core.runner.build_repo_map") as mock_build:
        runner._rebuild_repo_map_if_dirty()
        mock_build.assert_not_called()


def test_rebuild_if_dirty_fires_once_then_clears_flag(tmp_path: Path):
    runner = AgentRunner(
        project=_project(),
        run_id="r-1d",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    runner._repo_map_dirty = True
    with patch("core.runner.build_repo_map") as mock_build:
        mock_build.return_value = runner.workspace.repo_map
        runner._rebuild_repo_map_if_dirty()
        mock_build.assert_called_once()
    assert runner._repo_map_dirty is False


def test_multiple_commits_single_rebuild_at_transition(tmp_path: Path):
    """The actual cache-stability win: many commits mid-iteration → one rebuild."""
    runner = AgentRunner(
        project=_project(),
        run_id="r-1d",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )

    # Simulate three commits within a single agent's iteration.
    runner._repo_map_dirty = True  # commit 1
    runner._repo_map_dirty = True  # commit 2 (idempotent set)
    runner._repo_map_dirty = True  # commit 3

    # Agent transition fires the rebuild — exactly once.
    with patch("core.runner.build_repo_map") as mock_build:
        mock_build.return_value = runner.workspace.repo_map
        runner._rebuild_repo_map_if_dirty()
        assert mock_build.call_count == 1

    # Another commit AFTER the rebuild → flag set again, next transition rebuilds again.
    runner._repo_map_dirty = True
    with patch("core.runner.build_repo_map") as mock_build:
        mock_build.return_value = runner.workspace.repo_map
        runner._rebuild_repo_map_if_dirty()
        assert mock_build.call_count == 1


def test_rebuild_swallows_exceptions(tmp_path: Path):
    """A buggy build must not break the agent transition."""
    runner = AgentRunner(
        project=_project(),
        run_id="r-1d",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    runner._repo_map_dirty = True
    with patch("core.runner.build_repo_map", side_effect=RuntimeError("parse fail")):
        runner._rebuild_repo_map_if_dirty()  # must not raise
    assert runner._repo_map_dirty is False  # still cleared (in finally)
