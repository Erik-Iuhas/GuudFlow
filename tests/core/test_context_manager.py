"""Tests for core/context_manager.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.context_manager import (
    AgentContext,
    ContextManager,
    TIER_BUDGETS,
)
from core.file_workspace import FileWorkspace
from core.git_workspace import GitWorkspace
from core.repo_map import build
from core.signals import Signal, SignalType
from core.workspace import ContextSnapshot, Workspace


@pytest.fixture
def setup(tmp_path: Path):
    """Build a small repo, a workspace, and supporting helpers."""
    fw = FileWorkspace(tmp_path / "ws")
    # Synthesise a tiny codebase under working/.
    (fw.working / "auth.py").write_text(
        "class AuthManager:\n    def login(self):\n        pass\n"
    )
    (fw.working / "models" / "user.py").parent.mkdir(parents=True, exist_ok=True)
    (fw.working / "models" / "user.py").write_text(
        "class User:\n    pass\n"
    )
    (fw.working / "api.py").write_text(
        "from auth import AuthManager\n"
        "from models.user import User\n"
        "def login_endpoint():\n    pass\n"
    )

    repo_map = build(fw.working)
    ws = Workspace(run_id="r1", goal="x", files=fw)
    ws.repo_map = repo_map
    return ws, fw


# ── Defaults & happy path ───────────────────────────────────────────────────


async def test_get_context_returns_agent_context(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("implement login flow", agent_role="developer")
    assert isinstance(ctx, AgentContext)
    assert ctx.repo_map  # non-empty
    assert ctx.token_count > 0


async def test_repo_map_always_included_even_for_irrelevant_task(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("xyzzy nothing matches", agent_role="developer")
    assert ctx.repo_map  # rule 56: always included


async def test_default_budget_uses_solid_tier(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("login", agent_role="developer")
    assert ctx.token_count <= TIER_BUDGETS["solid"]


async def test_explicit_max_tokens_respected(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("login", agent_role="developer", max_tokens=200)
    assert ctx.token_count <= 200 + 5  # tiny slack for signal-summary rounding


async def test_tier_fast_smaller_budget(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx_fast = await cm.get_context("login", agent_role="developer", tier="fast")
    ctx_heavy = await cm.get_context("login", agent_role="developer", tier="heavy")
    assert ctx_fast.token_count <= TIER_BUDGETS["fast"]
    assert ctx_heavy.token_count <= TIER_BUDGETS["heavy"]


# ── Relevance ranking ───────────────────────────────────────────────────────


async def test_relevant_file_included_for_matching_task(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("AuthManager login flow", agent_role="developer")
    assert "auth.py" in ctx.relevant_files


async def test_unrelated_task_does_not_pull_files(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw, repo_map_budget_ratio=1.0)  # all budget on map
    ctx = await cm.get_context("zzz unrelated", agent_role="developer", max_tokens=1000)
    # No symbols match → no files included
    assert ctx.relevant_files == {}


# ── Iteration-aware diff ───────────────────────────────────────────────────


async def test_iteration_2_uses_diff_over_full_files(tmp_path: Path):
    fw = FileWorkspace(tmp_path / "ws")
    gw = GitWorkspace(fw.working)
    gw.init()
    (fw.working / "auth.py").write_text("class AuthManager:\n    pass\n")
    gw.commit("developer", "feat: auth")

    ws = Workspace(run_id="r1", goal="x", files=fw, iteration=2)
    ws.repo_map = build(fw.working)
    ws.git.agent_branches["developer"] = "agent/developer"

    # Make a change on the dev branch so diff has content
    gw.repo.git.checkout("agent/developer")
    (fw.working / "auth.py").write_text("class AuthManager:\n    def login(self):\n        pass\n")
    gw.commit("developer", "feat: add login")

    cm = ContextManager(ws, files=fw, git=gw)
    ctx = await cm.get_context("review changes", agent_role="developer", include_diff=True)
    assert ctx.git_diff is not None
    assert "login" in ctx.git_diff


async def test_iteration_1_does_not_use_diff(setup):
    ws, fw = setup
    ws.iteration = 1
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("review", agent_role="developer", include_diff=True)
    assert ctx.git_diff is None


async def test_include_diff_false_skips_diff(tmp_path: Path):
    fw = FileWorkspace(tmp_path / "ws")
    gw = GitWorkspace(fw.working)
    gw.init()
    (fw.working / "x.py").write_text("x = 1")
    gw.commit("developer", "feat: x")

    ws = Workspace(run_id="r1", goal="x", files=fw, iteration=2)
    ws.repo_map = build(fw.working)
    ws.git.agent_branches["developer"] = "agent/developer"

    cm = ContextManager(ws, files=fw, git=gw)
    ctx = await cm.get_context("x", agent_role="developer", include_diff=False)
    assert ctx.git_diff is None


# ── Recent signals ──────────────────────────────────────────────────────────


async def test_recent_signals_excludes_own_output(setup):
    ws, fw = setup
    ws.post_signal(Signal(type=SignalType.STARTED, summary="dev started", from_agent="developer"))
    ws.post_signal(Signal(type=SignalType.COMPLETE, summary="tester done", from_agent="tester"))

    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("x", agent_role="developer")
    summaries = [s.summary for s in ctx.recent_signals]
    assert "dev started" not in summaries  # own
    assert "tester done" in summaries


async def test_recent_signals_excludes_already_seen(setup):
    ws, fw = setup
    s = Signal(type=SignalType.COMPLETE, summary="x", from_agent="tester")
    ws.post_signal(s)
    ws.mark_seen(s.id, "developer")

    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("x", agent_role="developer")
    assert s not in ctx.recent_signals


async def test_recent_signals_limit_respected(setup):
    ws, fw = setup
    for i in range(10):
        ws.post_signal(Signal(type=SignalType.COMPLETE, summary=f"s{i}", from_agent="tester"))
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("x", agent_role="developer", recent_signals_limit=3)
    assert len(ctx.recent_signals) == 3


# ── Search ──────────────────────────────────────────────────────────────────


async def test_search_returns_ranked_matches(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    results = await cm.search("AuthManager")
    assert results
    assert results[0].path == "auth.py"
    assert results[0].relevance_score > 0


async def test_search_empty_for_no_matches(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    results = await cm.search("xyzzy_nothing")
    assert results == []


async def test_search_respects_limit(setup):
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    results = await cm.search("login", limit=1)
    assert len(results) <= 1


# ── Track 1: ContextSnapshot capture ────────────────────────────────────────


async def test_get_context_records_context_snapshot(setup):
    """get_context() must append exactly one ContextSnapshot to workspace.context_log."""
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("implement auth", agent_role="developer", tier="solid")

    assert len(ws.context_log) == 1
    snap = ws.context_log[0]
    assert isinstance(snap, ContextSnapshot)
    assert snap.role == "developer"
    assert snap.run_id == ws.run_id
    assert snap.tier == "solid"
    assert snap.used_tokens == ctx.token_count
    assert snap.task == "implement auth"


async def test_context_snapshot_relevant_files_match_context(setup):
    """ContextSnapshot.relevant_files must list the same paths as ctx.relevant_files."""
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("AuthManager login", agent_role="developer")

    snap = ws.context_log[-1]
    snapshot_paths = {path for path, _ in snap.relevant_files}
    context_paths = set(ctx.relevant_files.keys())
    assert snapshot_paths == context_paths


async def test_context_snapshot_used_tokens_correct(setup):
    """Snapshot used_tokens must equal ctx.token_count."""
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("login", agent_role="developer")

    snap = ws.context_log[-1]
    assert snap.used_tokens == ctx.token_count


async def test_context_snapshot_task_truncated_to_500(setup):
    """Snapshot task field must be truncated at 500 chars."""
    ws, fw = setup
    cm = ContextManager(ws, files=fw)
    long_task = "x" * 600
    await cm.get_context(long_task, agent_role="developer")

    snap = ws.context_log[-1]
    assert len(snap.task) == 500


async def test_context_snapshot_fires_persister(setup):
    """record_context_snapshot() inside get_context() must fire the persister."""
    ws, fw = setup
    fired: list[ContextSnapshot] = []
    ws._context_log_persister = fired.append

    cm = ContextManager(ws, files=fw)
    await cm.get_context("build tests", agent_role="developer")

    assert len(fired) == 1
    assert fired[0].role == "developer"


# ── Robustness ──────────────────────────────────────────────────────────────


async def test_empty_repo_map_returns_empty_context(tmp_path: Path):
    fw = FileWorkspace(tmp_path / "ws")
    ws = Workspace(run_id="r1", goal="x", files=fw)
    cm = ContextManager(ws, files=fw)
    ctx = await cm.get_context("anything", agent_role="developer")
    assert ctx.repo_map == ""
    assert ctx.relevant_files == {}


async def test_no_files_helper_means_no_full_contents(setup):
    ws, _ = setup
    cm = ContextManager(ws, files=None)
    ctx = await cm.get_context("AuthManager", agent_role="developer")
    assert ctx.relevant_files == {}
    assert ctx.repo_map  # repo map still rendered
