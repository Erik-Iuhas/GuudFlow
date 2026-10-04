"""Tests for core/runner.py: construction, team validation, the legacy refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import (
    LEGACY_HARNESS_RETIRED,
    AgentConfig,
    AgentRunner,
    ProjectConfig,
)


def _project(**overrides) -> ProjectConfig:
    base = dict(
        project_id="p1",
        goal="do a thing",
        agents=[],
        rbac={},
        max_iterations=3,
    )
    base.update(overrides)
    return ProjectConfig(**base)


async def test_runner_initialises_workspace_and_git(tmp_path: Path):
    runner = AgentRunner(
        project=_project(),
        run_id="r1",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    # File workspace created
    assert (tmp_path / "run" / "workspace" / "working").is_dir()
    # Git initialised
    assert (tmp_path / "run" / "workspace" / "working" / ".git").exists()


async def test_runner_attaches_board_persister(tmp_path: Path):
    """Posting a signal appends it to board.jsonl in the run root."""
    from core.signals import Signal, SignalType

    runner = AgentRunner(
        project=_project(), run_id="r1", run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner.workspace.post_signal(Signal(type=SignalType.STARTED, summary="hello", from_agent="x"))
    assert "hello" in (tmp_path / "run" / "board.jsonl").read_text(encoding="utf-8")


async def test_runner_refuses_the_retired_legacy_harness(tmp_path: Path):
    runner = AgentRunner(
        project=_project(harness="legacy"), run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    with pytest.raises(ValueError, match="legacy harness was retired"):
        await runner.run()
    assert "old runs remain viewable" in LEGACY_HARNESS_RETIRED


async def test_update_agent_config_changes_the_role_config(tmp_path: Path):
    proj = _project(agents=[AgentConfig(role="dev")])
    runner = AgentRunner(project=proj, run_id="r1", run_root=tmp_path / "run", llm=MockLLMClient([]))
    runner.update_agent_config("dev", {"model_tier": "heavy", "role": "ignored"})
    assert proj.agents[0].model_tier == "heavy"
    assert proj.agents[0].role == "dev"
    with pytest.raises(KeyError):
        runner.update_agent_config("nobody", {"model_tier": "fast"})


# ── Final-stage reviewer ────────────────────────────────────────────────────


def test_final_reviewer_auto_fills_depends_on(tmp_path: Path):
    """An is_final_reviewer agent with empty depends_on gets auto-deps
    on every other non-final-reviewer role at build time."""
    proj = _project(
        agents=[
            AgentConfig(role="dev"),
            AgentConfig(role="reviewer", depends_on=["dev"], is_reviewer=True),
            AgentConfig(role="architect", is_final_reviewer=True),
        ],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._prepare_team()
    architect_cfg = next(c for c in proj.agents if c.role == "architect")
    assert sorted(architect_cfg.depends_on) == ["dev", "reviewer"]


def test_final_reviewer_respects_explicit_depends_on(tmp_path: Path):
    """If the user explicitly set depends_on on a final reviewer, the
    auto-fill leaves it alone."""
    proj = _project(
        agents=[
            AgentConfig(role="dev"),
            AgentConfig(role="reviewer", depends_on=["dev"], is_reviewer=True),
            AgentConfig(
                role="architect",
                is_final_reviewer=True,
                depends_on=["reviewer"],  # explicit — only depend on reviewer
            ),
        ],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._prepare_team()
    architect_cfg = next(c for c in proj.agents if c.role == "architect")
    assert architect_cfg.depends_on == ["reviewer"]


def test_two_final_reviewers_do_not_depend_on_each_other(tmp_path: Path):
    """Multiple final reviewers each auto-dep on every NON-final role,
    not on each other (otherwise we'd serialise them artificially)."""
    proj = _project(
        agents=[
            AgentConfig(role="dev"),
            AgentConfig(role="security", is_final_reviewer=True),
            AgentConfig(role="architect", is_final_reviewer=True),
        ],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._prepare_team()
    sec_cfg = next(c for c in proj.agents if c.role == "security")
    arch_cfg = next(c for c in proj.agents if c.role == "architect")
    assert sec_cfg.depends_on == ["dev"]
    assert arch_cfg.depends_on == ["dev"]
