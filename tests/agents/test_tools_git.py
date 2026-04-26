"""Tests for agents/tools/git.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.git import GitDiff, GitLog, GitRead
from core.git_workspace import GitWorkspace


@pytest.fixture
def gw(tmp_path: Path) -> GitWorkspace:
    g = GitWorkspace(tmp_path / "working")
    g.init()
    return g


async def test_git_diff_returns_branch_changes(gw: GitWorkspace):
    (gw.working_dir / "auth.py").write_text("v1\n")
    gw.commit("developer", "feat: v1")
    res = await GitDiff(gw).run({"branch_a": "agent/developer", "branch_b": "main"})
    assert res["ok"] is True
    assert "auth.py" in res["diff"]


async def test_git_log_lists_recent_commits(gw: GitWorkspace):
    (gw.working_dir / "x.py").write_text("a")
    gw.commit("developer", "feat: a")
    res = await GitLog(gw).run({"branch": "agent/developer"})
    assert res["ok"] is True
    msgs = [c["message"] for c in res["commits"]]
    assert "feat: a" in msgs


async def test_git_read_at_branch(gw: GitWorkspace):
    (gw.working_dir / "x.py").write_text("v1")
    gw.commit("developer", "feat: x")
    res = await GitRead(gw).run({"branch": "agent/developer", "path": "x.py"})
    assert res["ok"] is True
    assert res["content"] == "v1"


async def test_git_read_missing_path(gw: GitWorkspace):
    res = await GitRead(gw).run({"branch": "main", "path": "nope.py"})
    assert "error" in res


async def test_git_read_missing_args(gw: GitWorkspace):
    res = await GitRead(gw).run({"branch": "main"})
    assert "error" in res
