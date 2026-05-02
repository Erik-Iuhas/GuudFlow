"""Track 4.2 — pr_mode tests.

Verifies the per-project pr_mode toggle:
- default is "manual"
- "auto" preserves the historical leaf-into-main merge
- "manual" merges leaf into an integration branch and posts PR_READY
  carrying a paste-ready `gh pr create` command
- the integration branch name uses an active story slug if any role has
  one open, else a short run-id-based name.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import SignalType
from core.tool_manifest import RoleRBAC
from core.workspace import StoryState


def _make_runner(
    agents: list[AgentConfig],
    tmp_path: Path,
    *,
    pr_mode: str | None = None,
    project_id: str = "p_prmode",
    run_id: str = "r_prmode_abcdef0123",
) -> AgentRunner:
    rbac = {c.role: RoleRBAC(role=c.role, allowed_scopes=[]) for c in agents}
    kwargs: dict = {}
    if pr_mode is not None:
        kwargs["pr_mode"] = pr_mode
    proj = ProjectConfig(
        project_id=project_id, goal="test pr mode behavior",
        agents=agents, rbac=rbac, **kwargs,
    )
    return AgentRunner(
        project=proj, run_id=run_id,
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )


def _commit_leaf(runner: AgentRunner) -> str:
    """Helper: simulate a single agent committing one file to its branch."""
    working = runner.files.working
    (working / "leaf.py").write_text("# leaf\n")
    runner.git.commit("dev", "feat: leaf work")
    runner.workspace.git.agent_branches["dev"] = "agent/dev"
    return "agent/dev"


# ── Defaults ────────────────────────────────────────────────────────────────


def test_pr_mode_default_is_manual() -> None:
    proj = ProjectConfig(project_id="p", goal="g")
    assert proj.pr_mode == "manual"


# ── auto path keeps today's behaviour ──────────────────────────────────────


async def test_pr_mode_auto_merges_into_main(tmp_path: Path) -> None:
    agents = [AgentConfig(role="dev", description="d", depends_on=[])]
    runner = _make_runner(agents, tmp_path, pr_mode="auto")
    _commit_leaf(runner)

    pre_main = runner.git.repo.heads["main"].commit.hexsha
    await runner._land_leaf_on_main()
    post_main = runner.git.repo.heads["main"].commit.hexsha

    assert pre_main != post_main, "auto mode should advance main"
    files = [
        b.name for b in runner.git.repo.heads["main"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "leaf.py" in files

    # No PR_READY signal in auto mode.
    pr_ready = [s for s in runner.workspace.board if s.type is SignalType.PR_READY]
    assert pr_ready == []


# ── manual path: stops at the integration branch ──────────────────────────


async def test_pr_mode_manual_merges_into_integration_branch(tmp_path: Path) -> None:
    agents = [AgentConfig(role="dev", description="d", depends_on=[])]
    runner = _make_runner(agents, tmp_path, pr_mode="manual")
    _commit_leaf(runner)

    pre_main = runner.git.repo.heads["main"].commit.hexsha
    await runner._land_leaf_on_main()
    post_main = runner.git.repo.heads["main"].commit.hexsha

    # main is unchanged.
    assert pre_main == post_main, "manual mode must not touch main"

    # Integration branch (run-based since no story is open) carries the work.
    expected_branch = f"flow/run-{runner.run_id[:10]}"
    head_names = [h.name for h in runner.git.repo.heads]
    assert expected_branch in head_names

    files = [
        b.name for b in runner.git.repo.heads[expected_branch].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "leaf.py" in files


async def test_pr_mode_manual_with_story_uses_story_slug(tmp_path: Path) -> None:
    agents = [AgentConfig(role="dev", description="d", depends_on=[])]
    runner = _make_runner(agents, tmp_path, pr_mode="manual")

    # Open a story for the dev role.
    story_id = "auth_redesign"
    runner.workspace.stories[story_id] = StoryState(
        story_id=story_id, name="Auth redesign", goal="rework auth",
    )
    runner.workspace.active_story["dev"] = story_id

    _commit_leaf(runner)
    await runner._land_leaf_on_main()

    expected_branch = "flow/story-auth-redesign"
    assert expected_branch in [h.name for h in runner.git.repo.heads]


# ── PR_READY signal carries the gh command ─────────────────────────────────


async def test_pr_mode_manual_emits_pr_ready_signal(tmp_path: Path) -> None:
    agents = [AgentConfig(role="dev", description="d", depends_on=[])]
    runner = _make_runner(agents, tmp_path, pr_mode="manual")
    _commit_leaf(runner)
    await runner._land_leaf_on_main()

    pr_ready = [s for s in runner.workspace.board if s.type is SignalType.PR_READY]
    assert len(pr_ready) == 1
    sig = pr_ready[0]
    payload = json.loads(sig.detail or "{}")
    expected_branch = f"flow/run-{runner.run_id[:10]}"
    assert payload["integration_branch"] == expected_branch
    assert payload["base_branch"] == "main"
    assert payload["leaf_branch"] == "agent/dev"
    assert payload["head_sha"]  # non-empty
    assert payload["gh_command"].startswith("gh pr create --base main --head ")
    assert expected_branch in payload["gh_command"]
    assert "title_suggestion" in payload


async def test_pr_mode_manual_idempotent_on_second_call(tmp_path: Path) -> None:
    """If the integration branch already contains the leaf's tip, manual mode
    should still surface PR_READY (so the UI can show the gh command on
    repeated views) without re-merging."""
    agents = [AgentConfig(role="dev", description="d", depends_on=[])]
    runner = _make_runner(agents, tmp_path, pr_mode="manual")
    _commit_leaf(runner)

    await runner._land_leaf_on_main()
    expected_branch = f"flow/run-{runner.run_id[:10]}"
    sha_after_first = runner.git.repo.heads[expected_branch].commit.hexsha

    await runner._land_leaf_on_main()
    sha_after_second = runner.git.repo.heads[expected_branch].commit.hexsha
    assert sha_after_first == sha_after_second

    pr_ready = [s for s in runner.workspace.board if s.type is SignalType.PR_READY]
    assert len(pr_ready) >= 1
