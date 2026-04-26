"""Tests for core/runner.py — internals."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient, MockTurn
from core.runner import (
    AgentConfig,
    AgentRunner,
    ProjectConfig,
    TIER_TO_MODEL,
    default_agent_factory,
)
from core.tool_manifest import RoleRBAC


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


def test_default_agent_factory_creates_base_agent():
    cfg = AgentConfig(role="x")
    agent = default_agent_factory(cfg)
    assert agent.role == "x"


def test_tier_model_mapping():
    assert "heavy" in TIER_TO_MODEL
    assert "solid" in TIER_TO_MODEL
    assert "fast" in TIER_TO_MODEL


async def test_runner_initialises_workspace_and_git(tmp_path: Path):
    proj = _project()
    llm = MockLLMClient([])
    runner = AgentRunner(
        project=proj,
        run_id="r1",
        run_root=tmp_path / "run",
        llm=llm,
    )
    # File workspace created
    assert (tmp_path / "run" / "workspace" / "working").is_dir()
    # Git initialised
    assert (tmp_path / "run" / "workspace" / "working" / ".git").exists()
    # Repo map built
    assert runner.workspace.repo_map is not None
    # Broker created
    assert runner.broker is not None


async def test_runner_builds_per_agent_manifest(tmp_path: Path):
    proj = _project(
        agents=[AgentConfig(
            role="dev",
            declared_local_tools=["fs:read", "fs:write"],
            model_tier="solid",
        )],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=["fs:write"])},
    )
    llm = MockLLMClient([])
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    agent = runner.agents["dev"]
    names = agent._manifest.names()
    assert "fs:read" in names      # baseline
    assert "fs:write" in names     # explicitly declared + RBAC granted


async def test_runner_excludes_undeclared_tools(tmp_path: Path):
    proj = _project(
        agents=[AgentConfig(
            role="dev",
            declared_local_tools=["fs:read"],  # NOT fs:write
        )],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=["fs:write"])},
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._build_agents()
    names = runner.agents["dev"]._manifest.names()
    assert "fs:read" in names
    assert "fs:write" not in names  # not declared → not in manifest


async def test_runner_assigns_model_from_tier(tmp_path: Path):
    proj = _project(
        agents=[
            AgentConfig(role="a", model_tier="heavy"),
            AgentConfig(role="b", model_tier="fast"),
        ],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._build_agents()
    assert runner.agents["a"]._model == TIER_TO_MODEL["heavy"]
    assert runner.agents["b"]._model == TIER_TO_MODEL["fast"]


async def test_runner_local_tools_instantiated_per_role(tmp_path: Path):
    proj = _project(
        agents=[AgentConfig(role="dev", declared_local_tools=["fs:read"])],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._build_agents()
    tools = runner.agents["dev"]._local_tools
    # Every registered tool is instantiated; manifest dictates which the
    # agent can call.
    assert "fs:read" in tools
    assert "fs:write" in tools  # instantiated, but not in manifest


async def test_runner_no_agents_returns_complete(tmp_path: Path):
    proj = _project(agents=[])
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    result = await runner.run()
    # An empty agent list is "all complete".
    assert result.status.value == "complete"
