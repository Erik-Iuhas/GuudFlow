"""Tests for Phase K.1 — runner reads project roadmap into agent prompts.

The runner reads `{project_dir}/context/roadmap.md` at construction
and threads its content into each agent's `_project_context`. The
agent's `effective_system_prompt()` includes a "PROJECT CONTEXT"
section between project rules and persona memory recall when the
field is set.

Empty roadmap → field stays empty → prompt layer is omitted entirely.
That's the default for any project without an explicit roadmap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.base import BaseAgent
from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


def _project() -> ProjectConfig:
    return ProjectConfig(
        project_id="p_ctx",
        goal="x",
        agents=[
            AgentConfig(
                role="developer",
                description="dev",
                system_prompt="x",
                declared_local_tools=["fs-read"],
                model_tier="solid",
            ),
        ],
        rbac={"developer": RoleRBAC(role="developer", allowed_scopes=[])},
        max_iterations=1,
    )


# ── runner reads roadmap.md ───────────────────────────────────────────────


def test_runner_reads_roadmap_when_present(tmp_path: Path):
    project_dir = tmp_path / "projects" / "p_ctx"
    (project_dir / "context").mkdir(parents=True)
    (project_dir / "context" / "roadmap.md").write_text(
        "## Goal\nBuild the thing.\n\n## Done\n- nothing yet.\n",
        encoding="utf-8",
    )

    runner = AgentRunner(
        project=_project(),
        run_id="r",
        run_root=tmp_path / "runs" / "r",
        llm=MockLLMClient([]),
        project_dir=project_dir,
    )

    assert "Build the thing" in runner._project_context
    assert "## Done" in runner._project_context


def test_runner_empty_when_no_project_dir(tmp_path: Path):
    runner = AgentRunner(
        project=_project(),
        run_id="r",
        run_root=tmp_path / "runs" / "r",
        llm=MockLLMClient([]),
    )
    assert runner._project_context == ""


def test_runner_empty_when_roadmap_missing(tmp_path: Path):
    project_dir = tmp_path / "projects" / "p_ctx"
    project_dir.mkdir(parents=True)
    runner = AgentRunner(
        project=_project(),
        run_id="r",
        run_root=tmp_path / "runs" / "r",
        llm=MockLLMClient([]),
        project_dir=project_dir,
    )
    assert runner._project_context == ""


def test_runner_empty_when_roadmap_is_whitespace_only(tmp_path: Path):
    """Pure whitespace roadmaps are treated the same as no roadmap.
    Belt-and-suspenders: the API endpoint already removes empty
    files, but a hand-edited file with just newlines shouldn't
    inject a noise-only prompt layer."""
    project_dir = tmp_path / "projects" / "p_ctx"
    (project_dir / "context").mkdir(parents=True)
    (project_dir / "context" / "roadmap.md").write_text(
        "   \n\n   \n", encoding="utf-8",
    )
    runner = AgentRunner(
        project=_project(),
        run_id="r",
        run_root=tmp_path / "runs" / "r",
        llm=MockLLMClient([]),
        project_dir=project_dir,
    )
    assert runner._project_context == ""


# ── agent prompt assembly ──────────────────────────────────────────────────


async def test_agent_prompt_includes_project_context(tmp_path: Path):
    """When the runner builds agents, each agent's _project_context
    is set; effective_system_prompt() includes the PROJECT CONTEXT
    section verbatim."""
    project_dir = tmp_path / "projects" / "p_ctx"
    (project_dir / "context").mkdir(parents=True)
    roadmap = "## Where we are\nAuth landed last week. Refresh tokens next.\n"
    (project_dir / "context" / "roadmap.md").write_text(roadmap, encoding="utf-8")

    runner = AgentRunner(
        project=_project(),
        run_id="r",
        run_root=tmp_path / "runs" / "r",
        llm=MockLLMClient([]),
        project_dir=project_dir,
    )
    runner._build_agents()
    agent = runner.agents["developer"]
    prompt = await agent.effective_system_prompt()

    assert "PROJECT CONTEXT" in prompt
    assert "Auth landed last week" in prompt


async def test_agent_prompt_omits_layer_when_no_roadmap(tmp_path: Path):
    """No roadmap → no PROJECT CONTEXT section in the prompt at all.
    Same effective prompt as today's runs."""
    runner = AgentRunner(
        project=_project(),
        run_id="r",
        run_root=tmp_path / "runs" / "r",
        llm=MockLLMClient([]),
    )
    runner._build_agents()
    agent = runner.agents["developer"]
    prompt = await agent.effective_system_prompt()

    assert "PROJECT CONTEXT" not in prompt


async def test_base_agent_prompt_layer_directly():
    """Unit-level: BaseAgent.effective_system_prompt picks up
    _project_context independently of the runner. Belt-and-suspenders
    so the layer is well-defined apart from the runner wiring."""
    agent = BaseAgent(role="x", description="x", system_prompt="base")
    prompt_no_ctx = await agent.effective_system_prompt()
    assert "PROJECT CONTEXT" not in prompt_no_ctx

    agent._project_context = "## Goal\nShip it.\n"
    prompt_with_ctx = await agent.effective_system_prompt()
    assert "PROJECT CONTEXT" in prompt_with_ctx
    assert "Ship it." in prompt_with_ctx
