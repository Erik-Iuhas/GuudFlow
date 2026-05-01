"""Integration tests for _premerge_upstream_deps.

Every agent with depends_on gets upstream branches merged into its own
branch before it runs — not just reviewers. On APPROVED the reviewer's
branch (carrying all upstream commits) merges into main: one merge commit,
not one per dep.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC


def _project_two_devs_one_reviewer() -> ProjectConfig:
    return ProjectConfig(
        project_id="p_premerge",
        goal="ship the feature",
        agents=[
            AgentConfig(role="dev_a", description="builds A", depends_on=[]),
            AgentConfig(role="dev_b", description="builds B", depends_on=[]),
            AgentConfig(
                role="reviewer", description="reviews",
                depends_on=["dev_a", "dev_b"], is_reviewer=True,
            ),
        ],
        rbac={
            "dev_a":    RoleRBAC(role="dev_a",    allowed_scopes=[]),
            "dev_b":    RoleRBAC(role="dev_b",    allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
        },
    )


@pytest.fixture
def runner_with_two_devs(tmp_path: Path) -> AgentRunner:
    runner = AgentRunner(
        project=_project_two_devs_one_reviewer(),
        run_id="r_premerge",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    # Simulate dev_a and dev_b having committed work to their branches.
    working = runner.files.working
    (working / "dev_a.py").write_text("from dev_a\n")
    runner.git.commit("dev_a", "feat: dev_a work")
    runner.workspace.git.agent_branches["dev_a"] = "agent/dev_a"

    (working / "dev_b.py").write_text("from dev_b\n")
    runner.git.commit("dev_b", "feat: dev_b work")
    runner.workspace.git.agent_branches["dev_b"] = "agent/dev_b"
    return runner


# ── Pre-merge happy path ──────────────────────────────────────────────────────


async def test_premerge_brings_upstream_onto_reviewer_branch(runner_with_two_devs):
    runner = runner_with_two_devs
    reviewer_cfg = next(c for c in runner.project.agents if c.role == "reviewer")

    all_clean, conflicted = await runner._premerge_upstream_deps(reviewer_cfg)
    assert all_clean is True
    assert conflicted == []

    # Reviewer's branch now contains files from both devs.
    repo = runner.git.repo
    repo.git.checkout("agent/reviewer")
    files_on_branch = [b.name for b in repo.heads["agent/reviewer"].commit.tree.traverse()
                       if b.type == "blob"]
    assert "dev_a.py" in files_on_branch
    assert "dev_b.py" in files_on_branch


async def test_premerge_noop_for_agent_with_no_deps(tmp_path: Path):
    """An agent with empty depends_on returns clean immediately."""
    proj = ProjectConfig(
        project_id="p_no_deps",
        goal="x",
        agents=[
            AgentConfig(role="dev_a", description="r", depends_on=[]),
        ],
        rbac={"dev_a": RoleRBAC(role="dev_a", allowed_scopes=[])},
    )
    runner = AgentRunner(
        project=proj, run_id="r_no_deps",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    cfg = proj.agents[0]
    all_clean, conflicted = await runner._premerge_upstream_deps(cfg)
    assert all_clean is True
    assert conflicted == []


async def test_premerge_applies_to_non_reviewer_with_deps(runner_with_two_devs):
    """Non-reviewer agents with depends_on also get upstream branches merged in."""
    runner = runner_with_two_devs
    # Add an integrator that depends on both devs but is not a reviewer.
    integrator_cfg = AgentConfig(
        role="integrator", description="integrates",
        depends_on=["dev_a", "dev_b"], is_reviewer=False,
    )
    all_clean, conflicted = await runner._premerge_upstream_deps(integrator_cfg)
    assert all_clean is True
    assert conflicted == []

    # Integrator's branch should contain files from both devs.
    repo = runner.git.repo
    files_on_branch = [
        b.name for b in repo.heads["agent/integrator"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "dev_a.py" in files_on_branch
    assert "dev_b.py" in files_on_branch


async def test_premerge_skips_dep_with_no_branch(tmp_path: Path):
    """If an upstream dep hasn't committed yet, skip silently."""
    proj = ProjectConfig(
        project_id="p_skip",
        goal="x",
        agents=[
            AgentConfig(role="dev_a", description="codes", depends_on=[]),
            AgentConfig(
                role="reviewer", description="r", depends_on=["dev_a"],
                is_reviewer=True,
            ),
        ],
        rbac={
            "dev_a":    RoleRBAC(role="dev_a", allowed_scopes=[]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
        },
    )
    runner = AgentRunner(
        project=proj, run_id="r_skip",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    # dev_a never committed; agent_branches is empty for it.
    reviewer_cfg = proj.agents[1]
    all_clean, conflicted = await runner._premerge_upstream_deps(reviewer_cfg)
    assert all_clean is True
    assert conflicted == []


# ── Conflict path ─────────────────────────────────────────────────────────────


async def test_premerge_conflict_aborts_and_reports(tmp_path: Path):
    """When two upstream branches conflict on a shared file, the merge is
    aborted (left in clean state) and the branch name is returned."""
    proj = _project_two_devs_one_reviewer()
    runner = AgentRunner(
        project=proj, run_id="r_conflict",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )

    working = runner.files.working
    # Both devs modify shared.py with conflicting content.
    (working / "shared.py").write_text("from dev_a\n")
    runner.git.commit("dev_a", "feat: dev_a edits shared")
    runner.workspace.git.agent_branches["dev_a"] = "agent/dev_a"

    # Manually checkout main, then dev_b branch, and write conflicting content.
    runner.git.repo.git.checkout("main")
    (working / "shared.py").write_text("from dev_b\n")
    runner.git.commit("dev_b", "feat: dev_b edits shared")
    runner.workspace.git.agent_branches["dev_b"] = "agent/dev_b"

    reviewer_cfg = proj.agents[2]
    all_clean, conflicted = await runner._premerge_upstream_deps(reviewer_cfg)

    # First merge (dev_a) succeeds because reviewer branch was empty at main.
    # Second merge (dev_b) conflicts on shared.py.
    assert all_clean is False
    assert any("dev_b" in b for b in conflicted)

    # Repo is in a clean state (no in-progress merge).
    assert "MERGE_HEAD" not in runner.git.repo.git.status()


# ── End-to-end: APPROVED merges reviewer → main (single commit) ─────────────


async def test_approved_merges_reviewer_branch_into_main_once(runner_with_two_devs):
    """After pre-merge + APPROVED, main should equal reviewer branch tip
    (a single merge commit on main, not one per dep)."""
    runner = runner_with_two_devs
    reviewer_cfg = next(c for c in runner.project.agents if c.role == "reviewer")

    # Pre-merge upstream into reviewer.
    await runner._premerge_upstream_deps(reviewer_cfg)

    # Simulate the APPROVED path's single merge.
    reviewer_branch = runner.workspace.git.agent_branches[reviewer_cfg.role]
    ok = runner.git.merge(reviewer_branch, into="main")
    assert ok is True

    # Both dev files are on main via the single reviewer-branch merge.
    main_files = [
        b.name for b in runner.git.repo.heads["main"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "dev_a.py" in main_files
    assert "dev_b.py" in main_files
