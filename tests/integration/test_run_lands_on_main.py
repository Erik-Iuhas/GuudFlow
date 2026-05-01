"""Verify _land_leaf_on_main merges the pipeline leaf into main at run end."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import SignalType
from core.tool_manifest import RoleRBAC


def _make_runner(agents: list[AgentConfig], tmp_path: Path) -> AgentRunner:
    rbac = {c.role: RoleRBAC(role=c.role, allowed_scopes=[]) for c in agents}
    proj = ProjectConfig(project_id="p_land", goal="test", agents=agents, rbac=rbac)
    return AgentRunner(
        project=proj, run_id="r_land",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


async def test_land_leaf_merges_final_reviewer_into_main(tmp_path: Path):
    """When is_final_reviewer is set, _land_leaf_on_main merges that branch."""
    agents = [
        AgentConfig(role="dev", description="codes", depends_on=[]),
        AgentConfig(role="reviewer", description="reviews",
                    depends_on=["dev"], is_reviewer=True, is_final_reviewer=True),
    ]
    runner = _make_runner(agents, tmp_path)
    working = runner.files.working

    # dev commits a file.
    (working / "work.py").write_text("# work\n")
    runner.git.commit("dev", "feat: work")
    runner.workspace.git.agent_branches["dev"] = "agent/dev"

    # Simulate reviewer pre-merging dev and then committing its own work.
    runner.git.agent_branch("reviewer")  # ensure branch exists
    runner.git.merge("agent/dev", into="agent/reviewer")
    runner.git.repo.git.checkout("agent/reviewer")
    (working / "review.py").write_text("# reviewed\n")
    runner.git.commit("reviewer", "feat: reviewer work")
    runner.workspace.git.agent_branches["reviewer"] = "agent/reviewer"

    initial_main_sha = runner.git.repo.heads["main"].commit.hexsha

    await runner._land_leaf_on_main()

    new_main_sha = runner.git.repo.heads["main"].commit.hexsha
    assert new_main_sha != initial_main_sha

    main_files = [
        b.name for b in runner.git.repo.heads["main"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "work.py" in main_files
    assert "review.py" in main_files


async def test_land_leaf_is_noop_if_main_already_up_to_date(tmp_path: Path):
    """If the reviewer's APPROVED already merged to main, _land_leaf_on_main skips."""
    agents = [
        AgentConfig(role="reviewer", description="reviews",
                    depends_on=[], is_reviewer=True, is_final_reviewer=True),
    ]
    runner = _make_runner(agents, tmp_path)
    working = runner.files.working

    (working / "done.py").write_text("# done\n")
    runner.git.commit("reviewer", "feat: done")
    runner.workspace.git.agent_branches["reviewer"] = "agent/reviewer"

    # Simulate APPROVED merge already having happened.
    runner.git.merge("agent/reviewer", into="main")
    main_sha_after_merge = runner.git.repo.heads["main"].commit.hexsha

    await runner._land_leaf_on_main()

    # Main must not have moved further.
    assert runner.git.repo.heads["main"].commit.hexsha == main_sha_after_merge


async def test_land_leaf_picks_sink_when_no_final_reviewer(tmp_path: Path):
    """Without is_final_reviewer, _land_leaf_on_main picks the dep-graph sink."""
    agents = [
        AgentConfig(role="dev_a", description="codes", depends_on=[]),
        AgentConfig(role="integrator", description="integrates",
                    depends_on=["dev_a"]),
    ]
    runner = _make_runner(agents, tmp_path)
    working = runner.files.working

    (working / "a.py").write_text("a\n")
    runner.git.commit("dev_a", "feat: a")
    runner.workspace.git.agent_branches["dev_a"] = "agent/dev_a"

    runner.git.agent_branch("integrator")  # ensure branch exists
    runner.git.merge("agent/dev_a", into="agent/integrator")
    runner.git.repo.git.checkout("agent/integrator")
    (working / "integrated.py").write_text("# integrated\n")
    runner.git.commit("integrator", "feat: integrated")
    runner.workspace.git.agent_branches["integrator"] = "agent/integrator"

    initial_main_sha = runner.git.repo.heads["main"].commit.hexsha
    await runner._land_leaf_on_main()

    assert runner.git.repo.heads["main"].commit.hexsha != initial_main_sha
    main_files = [
        b.name for b in runner.git.repo.heads["main"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "integrated.py" in main_files
