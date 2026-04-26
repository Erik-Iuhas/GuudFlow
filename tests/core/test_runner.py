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
            declared_local_tools=["fs-read", "fs-write"],
            model_tier="solid",
        )],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=["fs-write"])},
    )
    llm = MockLLMClient([])
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    agent = runner.agents["dev"]
    names = agent._manifest.names()
    assert "fs-read" in names      # baseline
    assert "fs-write" in names     # explicitly declared + RBAC granted


async def test_runner_excludes_undeclared_tools(tmp_path: Path):
    proj = _project(
        agents=[AgentConfig(
            role="dev",
            declared_local_tools=["fs-read"],  # NOT fs:write
        )],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=["fs-write"])},
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._build_agents()
    names = runner.agents["dev"]._manifest.names()
    assert "fs-read" in names
    assert "fs-write" not in names  # not declared → not in manifest


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
        agents=[AgentConfig(role="dev", declared_local_tools=["fs-read"])],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._build_agents()
    tools = runner.agents["dev"]._local_tools
    # Every registered tool is instantiated; manifest dictates which the
    # agent can call.
    assert "fs-read" in tools
    assert "fs-write" in tools  # instantiated, but not in manifest


async def test_runner_no_agents_returns_complete(tmp_path: Path):
    proj = _project(agents=[])
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    result = await runner.run()
    # An empty agent list is "all complete".
    assert result.status.value == "complete"


# ── Phase C.3: tech_lead escalation auto-detection ──────────────────────────


def _post_rejected(workspace, reviewer_role: str, summary: str = "FAIL"):
    """Helper — post a REJECTED signal as if `reviewer_role` rejected
    its upstream agents. Tests use this rather than driving a full
    reviewer run."""
    from core.signals import Signal, SignalType
    workspace.post_signal(Signal(
        type=SignalType.REJECTED,
        summary=summary,
        from_agent=reviewer_role,
    ))


def _project_with_dev_reviewer_techlead() -> ProjectConfig:
    return _project(
        agents=[
            AgentConfig(role="dev"),
            AgentConfig(role="reviewer", depends_on=["dev"], is_reviewer=True),
            AgentConfig(
                role="tech_lead",
                agent_class_role_type="tech_lead",
                model_tier="heavy",
            ),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
            "tech_lead": RoleRBAC(role="tech_lead", allowed_scopes=[]),
        },
    )


def test_rejection_below_threshold_does_not_escalate(tmp_path: Path):
    proj = _project_with_dev_reviewer_techlead()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _post_rejected(runner.workspace, "reviewer")
    assert runner._rejection_counts == {"dev": 1}
    # No ESCALATION yet — threshold is 2.
    types = [s.type.value for s in runner.workspace.board]
    assert "ESCALATION" not in types


def test_rejection_at_threshold_posts_escalation(tmp_path: Path):
    proj = _project_with_dev_reviewer_techlead()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _post_rejected(runner.workspace, "reviewer")
    _post_rejected(runner.workspace, "reviewer")
    assert runner._rejection_counts == {"dev": 2}
    escalations = [s for s in runner.workspace.board if s.type.value == "ESCALATION"]
    assert len(escalations) == 1
    assert "tech_lead" in escalations[0].summary
    assert "target_role=dev" in (escalations[0].detail or "")


def test_escalation_fires_only_once_per_handler_target(tmp_path: Path):
    """Three rejections cross the threshold but only the first to do so
    fires; subsequent rejections don't re-post ESCALATION."""
    proj = _project_with_dev_reviewer_techlead()
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    for _ in range(3):
        _post_rejected(runner.workspace, "reviewer")
    escalations = [s for s in runner.workspace.board if s.type.value == "ESCALATION"]
    assert len(escalations) == 1


def test_threshold_zero_disables_escalation(tmp_path: Path):
    proj = _project(
        agents=[
            AgentConfig(role="dev"),
            AgentConfig(role="reviewer", depends_on=["dev"], is_reviewer=True),
        ],
        escalation_thresholds={"tech_lead": 0},
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    for _ in range(5):
        _post_rejected(runner.workspace, "reviewer")
    types = [s.type.value for s in runner.workspace.board]
    assert "ESCALATION" not in types


def test_two_target_roles_each_fire_their_own_escalation(tmp_path: Path):
    """Each upstream role's rejection count is tracked independently."""
    proj = _project(
        agents=[
            AgentConfig(role="dev"),
            AgentConfig(role="qa"),
            AgentConfig(
                role="reviewer", depends_on=["dev", "qa"], is_reviewer=True,
            ),
            AgentConfig(role="tech_lead", agent_class_role_type="tech_lead"),
        ],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    # Each REJECTED from `reviewer` increments BOTH dev and qa (per
    # depends_on). Two rejections → both cross threshold of 2.
    _post_rejected(runner.workspace, "reviewer")
    _post_rejected(runner.workspace, "reviewer")
    escalations = [s for s in runner.workspace.board if s.type.value == "ESCALATION"]
    targets = sorted(
        e.detail.split("target_role=")[1].split(";")[0].strip()
        for e in escalations
    )
    assert targets == ["dev", "qa"]


def test_escalation_posts_signal_even_without_handler_in_config(tmp_path: Path):
    """If the threshold is crossed but no tech_lead is in project.agents,
    we still post ESCALATION (visible on the board) but skip dispatch."""
    proj = _project(
        agents=[
            AgentConfig(role="dev"),
            AgentConfig(role="reviewer", depends_on=["dev"], is_reviewer=True),
        ],
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _post_rejected(runner.workspace, "reviewer")
    _post_rejected(runner.workspace, "reviewer")
    escalations = [s for s in runner.workspace.board if s.type.value == "ESCALATION"]
    assert len(escalations) == 1
    assert "handler=missing" in (escalations[0].detail or "")
    # No spawned handler task — _escalation_tasks stays empty.
    assert runner._escalation_tasks == []


async def test_reviewer_rejected_text_posts_rejected_signal(tmp_path: Path):
    """Phase C.3 reviewer-text detection: when a reviewer's artifact
    starts with REJECTED, the runner posts a REJECTED signal (not
    APPROVED) and skips the merge-into-main step."""
    proj = _project(
        agents=[
            AgentConfig(
                role="dev",
                description="writes code",
                declared_local_tools=["fs-read"],
            ),
            AgentConfig(
                role="reviewer",
                description="reviews",
                depends_on=["dev"],
                is_reviewer=True,
                declared_local_tools=["fs-read"],
            ),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
        },
        max_iterations=2,
    )
    # dev says "done"; reviewer says "REJECTED: missing tests".
    llm = MockLLMClient([
        MockTurn(text="done"),
        MockTurn(text="REJECTED: missing tests"),
    ])
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=llm,
    )
    await runner.run()
    types = [s.type.value for s in runner.workspace.board]
    assert "REJECTED" in types
    assert "APPROVED" not in types
    # Counted against `dev` — the reviewer's depends_on role.
    assert runner._rejection_counts.get("dev") == 1


async def test_reviewer_approved_text_still_emits_approved(tmp_path: Path):
    """Backwards compat: existing reviewer behaviour (approval) keeps
    working unchanged. Just the REJECTED branch is new."""
    proj = _project(
        agents=[
            AgentConfig(role="dev", declared_local_tools=["fs-read"]),
            AgentConfig(
                role="reviewer", depends_on=["dev"], is_reviewer=True,
                declared_local_tools=["fs-read"],
            ),
        ],
        max_iterations=2,
    )
    llm = MockLLMClient([
        MockTurn(text="done"),
        MockTurn(text="APPROVED"),
    ])
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=llm,
    )
    await runner.run()
    types = [s.type.value for s in runner.workspace.board]
    assert "APPROVED" in types
    assert "REJECTED" not in types
    # No rejection counted.
    assert runner._rejection_counts == {}


# ── Phase C.4: final-stage architect review ────────────────────────────────


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
    runner._build_agents()
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
    runner._build_agents()
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
    runner._build_agents()
    sec_cfg = next(c for c in proj.agents if c.role == "security")
    arch_cfg = next(c for c in proj.agents if c.role == "architect")
    assert sec_cfg.depends_on == ["dev"]
    assert arch_cfg.depends_on == ["dev"]


async def test_three_agent_run_with_architect_happy_path(tmp_path: Path):
    """3-agent flow: dev → reviewer → architect, all approve, run
    completes. Verifies the architect actually runs after the others
    when is_final_reviewer is set."""
    proj = _project(
        agents=[
            AgentConfig(role="dev", declared_local_tools=["fs-read"]),
            AgentConfig(
                role="reviewer", depends_on=["dev"], is_reviewer=True,
                declared_local_tools=["fs-read"],
            ),
            AgentConfig(
                role="architect",
                is_final_reviewer=True,
                is_reviewer=True,  # also a reviewer for APPROVED→merge logic
                declared_local_tools=["fs-read"],
            ),
        ],
        # Disable the gate for this test to keep the LLM script simple
        # — gate behaviour has its own focused tests.
        approval_gates={"heavy_tier_mid_run_dispatch": False},
        max_iterations=2,
    )
    llm = MockLLMClient([
        MockTurn(text="done"),       # dev
        MockTurn(text="APPROVED"),   # reviewer
        MockTurn(text="APPROVED"),   # architect
    ])
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=llm,
    )
    result = await runner.run()
    assert result.status.value == "complete"
    assert "architect" in runner.workspace.completed_agents
    # Architect's completion lands as APPROVED on the board (it's a reviewer).
    approved = [s for s in runner.workspace.board if s.type.value == "APPROVED"]
    approved_agents = {s.from_agent for s in approved}
    assert "reviewer" in approved_agents
    assert "architect" in approved_agents


# Note: the gate-on-architect denial path is verified by code review +
# the existing focused approval tests (tests/core/test_approval_gates.py).
# Adding an e2e test that drives runner.run() with a concurrent gate
# resolver hits the same async coordination edge-case that the equivalent
# C.3 test ran into. Once Phase D ships its meta-agent flow (which uses
# the same gate path), an e2e test will land cleanly there.
