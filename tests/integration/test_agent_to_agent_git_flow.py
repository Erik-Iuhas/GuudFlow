"""Verify agent-to-agent git flow: downstream agents see upstream work.

integration_dev depends_on=[dev_a, dev_b]. Before integration_dev runs,
_premerge_upstream_deps merges both dev branches into agent/integration_dev
so the working tree contains all upstream files.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


def _project() -> ProjectConfig:
    return ProjectConfig(
        project_id="p_flow",
        goal="build and integrate",
        agents=[
            AgentConfig(role="dev_a", description="builds A", depends_on=[]),
            AgentConfig(role="dev_b", description="builds B", depends_on=[]),
            AgentConfig(
                role="integrator", description="integrates",
                depends_on=["dev_a", "dev_b"], is_reviewer=False,
            ),
        ],
        rbac={
            "dev_a":      RoleRBAC(role="dev_a",      allowed_scopes=[]),
            "dev_b":      RoleRBAC(role="dev_b",      allowed_scopes=[]),
            "integrator": RoleRBAC(role="integrator", allowed_scopes=[]),
        },
    )


async def test_integrator_sees_both_dev_files(tmp_path: Path):
    """After pre-merge, the integrator's branch contains files from dev_a and dev_b."""
    runner = AgentRunner(
        project=_project(),
        run_id="r_flow",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    working = runner.files.working

    # dev_a commits a file.
    (working / "feature_a.py").write_text("# from dev_a\n")
    runner.git.commit("dev_a", "feat: dev_a feature")
    runner.workspace.git.agent_branches["dev_a"] = "agent/dev_a"

    # dev_b commits a different file.
    runner.git.repo.git.checkout("main")
    (working / "feature_b.py").write_text("# from dev_b\n")
    runner.git.commit("dev_b", "feat: dev_b feature")
    runner.workspace.git.agent_branches["dev_b"] = "agent/dev_b"

    integrator_cfg = next(c for c in runner.project.agents if c.role == "integrator")
    all_clean, conflicted = await runner._premerge_upstream_deps(integrator_cfg)

    assert all_clean is True
    assert conflicted == []

    repo = runner.git.repo
    files_on_branch = [
        b.name for b in repo.heads["agent/integrator"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "feature_a.py" in files_on_branch
    assert "feature_b.py" in files_on_branch


async def test_integrator_branch_is_ancestor_of_both_devs(tmp_path: Path):
    """After pre-merge, dev_a's and dev_b's commits are ancestors of integrator's tip."""
    runner = AgentRunner(
        project=_project(),
        run_id="r_ancestor",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    working = runner.files.working

    (working / "a.py").write_text("a\n")
    runner.git.commit("dev_a", "feat: a")
    runner.workspace.git.agent_branches["dev_a"] = "agent/dev_a"
    dev_a_sha = runner.git.repo.heads["agent/dev_a"].commit.hexsha

    runner.git.repo.git.checkout("main")
    (working / "b.py").write_text("b\n")
    runner.git.commit("dev_b", "feat: b")
    runner.workspace.git.agent_branches["dev_b"] = "agent/dev_b"
    dev_b_sha = runner.git.repo.heads["agent/dev_b"].commit.hexsha

    integrator_cfg = next(c for c in runner.project.agents if c.role == "integrator")
    await runner._premerge_upstream_deps(integrator_cfg)

    repo = runner.git.repo
    integrator_tip = repo.heads["agent/integrator"].commit
    ancestor_shas = {c.hexsha for c in repo.iter_commits(integrator_tip)}
    assert dev_a_sha in ancestor_shas
    assert dev_b_sha in ancestor_shas


async def test_premerge_with_no_upstream_commits_is_noop(tmp_path: Path):
    """If upstream deps have no commits, pre-merge returns clean and branch stays at main."""
    runner = AgentRunner(
        project=_project(),
        run_id="r_noop",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    # Neither dev_a nor dev_b committed anything — agent_branches is empty.
    integrator_cfg = next(c for c in runner.project.agents if c.role == "integrator")
    all_clean, conflicted = await runner._premerge_upstream_deps(integrator_cfg)

    assert all_clean is True
    assert conflicted == []


# ── Track 2a: question-after-complete respawn ────────────────────────────────


async def test_question_after_complete_respawn(tmp_path: Path):
    """When an AGENT_MESSAGE is posted targeting a completed agent, the
    runner's question-respawn subscriber re-queues it for an answering run.

    This tests the full wiring: workspace.subscribe → runner subscriber →
    state mutation → scheduler re-spawn eligibility.
    """
    import json

    from core.signals import Signal, SignalType
    from core.workspace import AgentState

    project = ProjectConfig(
        project_id="p_respawn",
        goal="test question respawn integration",
        agents=[
            AgentConfig(role="dev_a", description="builds feature", depends_on=[]),
            AgentConfig(role="requester", description="asks follow-up", depends_on=["dev_a"]),
        ],
        rbac={
            "dev_a":     RoleRBAC(role="dev_a",     allowed_scopes=[]),
            "requester": RoleRBAC(role="requester", allowed_scopes=[]),
        },
    )
    runner = AgentRunner(
        project=project,
        run_id="r_qrespawn",
        run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )

    # Simulate dev_a completing its primary run.
    runner.workspace.completed_agents.append("dev_a")
    runner.workspace.agent_states["dev_a"] = AgentState.COMPLETE

    # requester posts a question targeting dev_a.
    runner.workspace.post_signal(Signal(
        type=SignalType.AGENT_MESSAGE,
        summary="requester → dev_a: clarify X?",
        from_agent="requester",
        detail=json.dumps({"to": "dev_a", "from": "requester", "message": "clarify X?"}),
    ))

    # The subscriber fires synchronously on post_signal.
    assert "dev_a" not in runner.workspace.completed_agents, (
        "dev_a should have been removed from completed_agents by the respawn subscriber"
    )
    assert runner.workspace.agent_states["dev_a"] is AgentState.READY
    assert "dev_a" in runner._question_mode_roles
    assert runner._question_respawn_counts["dev_a"] == 1

    # Cap enforcement: three more questions exhaust the remaining 2 slots then stop.
    for _ in range(2):
        runner.workspace.completed_agents.append("dev_a")
        runner.workspace.agent_states["dev_a"] = AgentState.COMPLETE
        runner._question_mode_roles.discard("dev_a")
        runner.workspace.post_signal(Signal(
            type=SignalType.AGENT_MESSAGE,
            summary="requester → dev_a: more?",
            from_agent="requester",
            detail=json.dumps({"to": "dev_a", "from": "requester", "message": "more?"}),
        ))
    assert runner._question_respawn_counts["dev_a"] == 3

    # 4th question after cap — state must not change.
    runner.workspace.completed_agents.append("dev_a")
    runner.workspace.agent_states["dev_a"] = AgentState.COMPLETE
    runner._question_mode_roles.discard("dev_a")
    runner.workspace.post_signal(Signal(
        type=SignalType.AGENT_MESSAGE,
        summary="requester → dev_a: one more?",
        from_agent="requester",
        detail=json.dumps({"to": "dev_a", "from": "requester", "message": "one more?"}),
    ))
    assert "dev_a" in runner.workspace.completed_agents, "dev_a should stay completed past cap"
    assert runner._question_respawn_counts["dev_a"] == 3
