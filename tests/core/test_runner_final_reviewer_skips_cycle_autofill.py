"""Ensure is_final_reviewer auto-fill skips roles that already depend on
the reviewer, preventing the cycle that causes STUCK at iteration 0.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.runner import AgentConfig, AgentRunner, ProjectConfig, _detect_dependency_cycle
from core.llm import MockLLMClient


def _make_runner(agents: list[AgentConfig], tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(project_id="p1", goal="test", agents=agents, rbac={})
    return AgentRunner(project=proj, run_id="r1", run_root=tmp_path / "run", llm=MockLLMClient([]))


# ── _detect_dependency_cycle ─────────────────────────────────────────────────


def test_detect_cycle_linear_no_cycle():
    agents = [
        AgentConfig(role="a", depends_on=[]),
        AgentConfig(role="b", depends_on=["a"]),
        AgentConfig(role="c", depends_on=["b"]),
    ]
    assert _detect_dependency_cycle(agents) is None


def test_detect_cycle_two_node_cycle():
    agents = [
        AgentConfig(role="a", depends_on=["b"]),
        AgentConfig(role="b", depends_on=["a"]),
    ]
    assert _detect_dependency_cycle(agents) is not None


def test_detect_cycle_self_loop():
    agents = [AgentConfig(role="a", depends_on=["a"])]
    assert _detect_dependency_cycle(agents) is not None


def test_detect_cycle_three_node_chain():
    agents = [
        AgentConfig(role="a", depends_on=["c"]),
        AgentConfig(role="b", depends_on=["a"]),
        AgentConfig(role="c", depends_on=["b"]),
    ]
    cyc = _detect_dependency_cycle(agents)
    assert cyc is not None
    assert len(cyc) > 0


# ── auto-fill with cycle prevention ─────────────────────────────────────────


def test_autofill_final_reviewer_no_inbound_deps(tmp_path: Path):
    """Normal case: final reviewer has no inbound deps → auto-fill all non-finals."""
    agents = [
        AgentConfig(role="dev_a", depends_on=[], is_final_reviewer=False),
        AgentConfig(role="dev_b", depends_on=[], is_final_reviewer=False),
        AgentConfig(role="reviewer", depends_on=[], is_final_reviewer=True),
    ]
    runner = _make_runner(agents, tmp_path)
    runner._prepare_team()
    rev_cfg = next(c for c in runner.project.agents if c.role == "reviewer")
    assert sorted(rev_cfg.depends_on) == ["dev_a", "dev_b"]


def test_autofill_skips_inbound_dep_to_prevent_cycle(tmp_path: Path):
    """game_architect (final reviewer) with devs that explicitly depend on it.
    Auto-fill must skip those devs to avoid a cycle.
    """
    agents = [
        AgentConfig(role="game_architect", depends_on=[], is_final_reviewer=True),
        AgentConfig(role="dev_a", depends_on=["game_architect"], is_final_reviewer=False),
        AgentConfig(role="dev_b", depends_on=["game_architect"], is_final_reviewer=False),
    ]
    runner = _make_runner(agents, tmp_path)
    runner._prepare_team()

    arch_cfg = next(c for c in runner.project.agents if c.role == "game_architect")
    # dev_a and dev_b depend on game_architect → auto-fill must not add them back
    assert "dev_a" not in arch_cfg.depends_on
    assert "dev_b" not in arch_cfg.depends_on
    # No cycle exists
    assert _detect_dependency_cycle(runner.project.agents) is None


def test_autofill_partial_inbound(tmp_path: Path):
    """Only some devs depend on the final reviewer — those are blocked; others are included."""
    agents = [
        AgentConfig(role="reviewer", depends_on=[], is_final_reviewer=True),
        AgentConfig(role="dev_a", depends_on=["reviewer"], is_final_reviewer=False),
        AgentConfig(role="dev_b", depends_on=[], is_final_reviewer=False),
    ]
    runner = _make_runner(agents, tmp_path)
    runner._prepare_team()

    rev_cfg = next(c for c in runner.project.agents if c.role == "reviewer")
    assert "dev_a" not in rev_cfg.depends_on   # would cause cycle
    assert "dev_b" in rev_cfg.depends_on        # safe to include
    assert _detect_dependency_cycle(runner.project.agents) is None


def test_autofill_skips_transitive_inbound_dep(tmp_path: Path):
    """Real-world topology from "4x4 pac man":
        game_architect (final reviewer, depends_on=[])
        core_game_dev, animation_dev → depend on game_architect
        integration_dev → depends on devs
        qa_and_docs_reviewer → depends on integration_dev

    Without transitive blocker check, auto-fill would add integration_dev or
    qa_and_docs_reviewer to game_architect.depends_on, creating a cycle
    through the dev chain.
    """
    agents = [
        AgentConfig(role="game_architect", depends_on=[], is_final_reviewer=True),
        AgentConfig(role="core_game_dev", depends_on=["game_architect"], is_final_reviewer=False),
        AgentConfig(role="animation_dev", depends_on=["game_architect"], is_final_reviewer=False),
        AgentConfig(role="integration_dev", depends_on=["core_game_dev", "animation_dev"], is_final_reviewer=False),
        AgentConfig(role="qa_and_docs_reviewer", depends_on=["integration_dev"], is_final_reviewer=False),
    ]
    runner = _make_runner(agents, tmp_path)
    runner._prepare_team()

    arch_cfg = next(c for c in runner.project.agents if c.role == "game_architect")
    # Every other role transitively depends on game_architect → all blocked
    assert arch_cfg.depends_on == []
    # Acyclic
    assert _detect_dependency_cycle(runner.project.agents) is None


def test_explicit_depends_on_never_overwritten(tmp_path: Path):
    """If final reviewer already has depends_on set, auto-fill must not touch it."""
    agents = [
        AgentConfig(role="reviewer", depends_on=["dev_b"], is_final_reviewer=True),
        AgentConfig(role="dev_a", depends_on=[], is_final_reviewer=False),
        AgentConfig(role="dev_b", depends_on=[], is_final_reviewer=False),
    ]
    runner = _make_runner(agents, tmp_path)
    runner._prepare_team()

    rev_cfg = next(c for c in runner.project.agents if c.role == "reviewer")
    assert rev_cfg.depends_on == ["dev_b"]
