"""Handcrafted cyclic configs must emit a FAILED signal and raise RuntimeError
instead of silently going STUCK.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.llm import MockLLMClient
from core.signals import SignalType


def _make_runner(agents: list[AgentConfig], tmp_path: Path) -> AgentRunner:
    proj = ProjectConfig(project_id="p1", goal="test", agents=agents, rbac={})
    return AgentRunner(project=proj, run_id="r1", run_root=tmp_path / "run", llm=MockLLMClient([]))


def test_cycle_raises_runtime_error(tmp_path: Path):
    agents = [
        AgentConfig(role="a", depends_on=["b"]),
        AgentConfig(role="b", depends_on=["a"]),
    ]
    runner = _make_runner(agents, tmp_path)
    with pytest.raises(RuntimeError, match="dependency cycle detected"):
        runner._prepare_team()


def test_cycle_emits_failed_signal(tmp_path: Path):
    agents = [
        AgentConfig(role="a", depends_on=["b"]),
        AgentConfig(role="b", depends_on=["a"]),
    ]
    runner = _make_runner(agents, tmp_path)
    with pytest.raises(RuntimeError):
        runner._prepare_team()

    failed = [s for s in runner.workspace.board if s.type == SignalType.FAILED]
    assert len(failed) == 1
    assert "cycle" in failed[0].summary.lower()


def test_three_way_cycle_detected(tmp_path: Path):
    agents = [
        AgentConfig(role="a", depends_on=["c"]),
        AgentConfig(role="b", depends_on=["a"]),
        AgentConfig(role="c", depends_on=["b"]),
    ]
    runner = _make_runner(agents, tmp_path)
    with pytest.raises(RuntimeError, match="dependency cycle detected"):
        runner._prepare_team()


def test_no_cycle_builds_normally(tmp_path: Path):
    agents = [
        AgentConfig(role="a", depends_on=[]),
        AgentConfig(role="b", depends_on=["a"]),
    ]
    runner = _make_runner(agents, tmp_path)
    runner._prepare_team()   # must not raise
    assert True


def test_a_role_waiting_on_a_missing_role_stops_the_run(tmp_path: Path):
    """A planner replaced by a tech lead must not leave builders waiting on 'planner'."""
    agents = [
        AgentConfig(role="tech_lead"),
        AgentConfig(role="builder", depends_on=["planner"]),
        AgentConfig(role="reviewer", depends_on=["builder"], review_of=["builder"]),
    ]
    runner = _make_runner(agents, tmp_path)
    with pytest.raises(RuntimeError, match="builder: depends_on names planner"):
        runner._prepare_team()
    failed = [s for s in runner.workspace.board if s.type == SignalType.FAILED]
    assert failed and "depends_on names planner" in failed[0].summary
