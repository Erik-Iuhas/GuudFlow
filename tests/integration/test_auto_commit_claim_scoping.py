"""Auto-commit scopes staging by FILE_CLAIM ownership (parallel-race fix).

Diagnosed in run_544c0dd23d: 3 IC devs (game_logic_dev, renderer_dev,
audio_dev) ran in parallel, sharing one working tree. The previous
`_auto_commit_locked` called `git.commit(role, message=...)` without
`files=`, falling through to `git add -A` — whoever auto-committed
first swept up every dirty file from every parallel agent.

audio_dev's 2nd commit grabbed renderer_dev's full src/render/* AND
game_logic_dev's src/state/* + src/systems/* (24 files that weren't
audio's). Then the other devs' auto-commits found nothing dirty and
posted TASK_OUT_OF_SCOPE warnings.

The fix: read the FILE_CLAIM signals this role posted this run and
stage ONLY those paths. Falls back to legacy `git add -A` when the
role has no claims (sequential / single-agent runs).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC


def _project_two_devs() -> ProjectConfig:
    return ProjectConfig(
        project_id="p_claim_scope",
        goal="ship two domains",
        agents=[
            AgentConfig(role="dev_a", description="domain A", depends_on=[]),
            AgentConfig(role="dev_b", description="domain B", depends_on=[]),
        ],
        rbac={
            "dev_a": RoleRBAC(role="dev_a", allowed_scopes=[]),
            "dev_b": RoleRBAC(role="dev_b", allowed_scopes=[]),
        },
    )


def _post_claim(runner: AgentRunner, role: str, path: str) -> None:
    """Simulate a FILE_CLAIM signal that a write tool would post before
    creating a file."""
    runner.workspace.post_signal(Signal(
        type=SignalType.FILE_CLAIM,
        summary=f"{role}: claim {path}",
        from_agent=role,
        detail=json.dumps({
            "path": path, "intent": "create",
            "estimated_duration_s": 60.0, "reason": "",
        }),
    ))


def _post_release(runner: AgentRunner, role: str, path: str) -> None:
    runner.workspace.post_signal(Signal(
        type=SignalType.FILE_RELEASE,
        summary=f"{role}: release {path}",
        from_agent=role,
        detail=json.dumps({"path": path}),
    ))


# ── Race fix: only stage paths this role claimed ──────────────────────────


@pytest.mark.asyncio
async def test_auto_commit_stages_only_paths_role_claimed(tmp_path: Path):
    """The headline race fix. Two devs both wrote to the working tree.
    dev_a's auto_commit must stage ONLY dev_a's claimed files, leaving
    dev_b's dirty files in the tree for dev_b's auto_commit."""
    runner = AgentRunner(
        project=_project_two_devs(), run_id="r_scope_a",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    working = runner.files.working

    # Both devs write files to the SHARED working tree.
    (working / "a.py").write_text("from dev_a\n")
    (working / "b.py").write_text("from dev_b\n")

    # Each dev claims their OWN files (release ordering doesn't matter —
    # the historical CLAIM record is what proves ownership).
    _post_claim(runner, "dev_a", "a.py")
    _post_release(runner, "dev_a", "a.py")
    _post_claim(runner, "dev_b", "b.py")
    _post_release(runner, "dev_b", "b.py")

    # dev_a auto-commits. Should stage ONLY a.py.
    sha_a = await runner._auto_commit("dev_a", runner.workspace)
    assert sha_a is not None

    # b.py must STILL be dirty after dev_a's commit (dev_b's file).
    repo = runner.git.repo
    status_after_a = repo.git.status("--porcelain").splitlines()
    assert any("b.py" in line for line in status_after_a), \
        f"dev_b's file should still be dirty; status={status_after_a}"

    # dev_b auto-commits. Now b.py lands.
    sha_b = await runner._auto_commit("dev_b", runner.workspace)
    assert sha_b is not None

    # Each commit's tree contains EXACTLY the claimed file.
    a_files = [
        b.name for b in repo.heads["agent/dev_a"].commit.tree.traverse()
        if b.type == "blob"
    ]
    b_files = [
        b.name for b in repo.heads["agent/dev_b"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "a.py" in a_files
    assert "b.py" not in a_files, "dev_a stole dev_b's file"
    assert "b.py" in b_files
    assert "a.py" not in b_files, "dev_b would steal dev_a's file"


@pytest.mark.asyncio
async def test_auto_commit_uses_release_history_not_active_claims(tmp_path: Path):
    """FILE_RELEASE fires AFTER staging in the real flow, so by commit
    time `active_claims` is empty. The scope filter MUST consult the
    historical FILE_CLAIM signals, not just the currently-active ones."""
    runner = AgentRunner(
        project=_project_two_devs(), run_id="r_release_history",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    working = runner.files.working
    (working / "a.py").write_text("x = 1\n")

    # Claim AND release before commit — simulates the real ordering.
    _post_claim(runner, "dev_a", "a.py")
    _post_release(runner, "dev_a", "a.py")

    # No active claims at this point — but historical claim still
    # proves ownership.
    sha = await runner._auto_commit("dev_a", runner.workspace)
    assert sha is not None
    files = [
        b.name for b in runner.git.repo.heads["agent/dev_a"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "a.py" in files


# ── No-claim fallback: legacy/sequential runs unchanged ───────────────────


@pytest.mark.asyncio
async def test_auto_commit_falls_back_to_add_all_when_no_claims(tmp_path: Path):
    """A role with zero FILE_CLAIM signals (legacy single-agent run,
    or an agent using a write tool that doesn't auto-claim) gets the
    old `git add -A` behaviour. Preserves backward compatibility for
    every test that doesn't post claims explicitly."""
    runner = AgentRunner(
        project=_project_two_devs(), run_id="r_fallback",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    working = runner.files.working
    (working / "x.py").write_text("x = 1\n")
    # NOTE: no FILE_CLAIM signals posted.
    sha = await runner._auto_commit("dev_a", runner.workspace)
    assert sha is not None
    files = [
        b.name for b in runner.git.repo.heads["agent/dev_a"].commit.tree.traverse()
        if b.type == "blob"
    ]
    assert "x.py" in files


# ── Helper: returns claimed paths from board history ──────────────────────


def test_claimed_paths_for_role_walks_board_history(tmp_path: Path):
    """Direct unit test of the helper. Returns sorted, deduplicated
    paths from FILE_CLAIM signals attributed to the role."""
    runner = AgentRunner(
        project=_project_two_devs(), run_id="r_helper",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    _post_claim(runner, "dev_a", "z.py")
    _post_claim(runner, "dev_a", "a.py")
    _post_claim(runner, "dev_a", "z.py")  # duplicate
    _post_claim(runner, "dev_b", "b.py")  # different role

    paths = runner._claimed_paths_for_role("dev_a")
    assert paths == ["a.py", "z.py"]  # sorted, deduped, only dev_a


def test_claimed_paths_for_role_handles_malformed_detail(tmp_path: Path):
    """Defensive: a malformed FILE_CLAIM detail (bad JSON, missing
    path) shouldn't crash the helper — just skip the bad entries."""
    runner = AgentRunner(
        project=_project_two_devs(), run_id="r_malformed",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner.workspace.post_signal(Signal(
        type=SignalType.FILE_CLAIM, from_agent="dev_a",
        summary="bad", detail="{not json",
    ))
    runner.workspace.post_signal(Signal(
        type=SignalType.FILE_CLAIM, from_agent="dev_a",
        summary="missing path", detail=json.dumps({"intent": "create"}),
    ))
    _post_claim(runner, "dev_a", "ok.py")
    assert runner._claimed_paths_for_role("dev_a") == ["ok.py"]


# ── Edge case: claim exists but file isn't dirty ──────────────────────────


@pytest.mark.asyncio
async def test_auto_commit_returns_none_when_claimed_paths_arent_dirty(
    tmp_path: Path,
):
    """A role might have CLAIM signals from an earlier iteration whose
    files have already been committed. If nothing claimed-AND-dirty
    is in the tree, return None (no commit) — don't crash."""
    runner = AgentRunner(
        project=_project_two_devs(), run_id="r_clean",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    # Claim a path but never write it (file doesn't exist on disk).
    _post_claim(runner, "dev_a", "phantom.py")
    sha = await runner._auto_commit("dev_a", runner.workspace)
    assert sha is None
