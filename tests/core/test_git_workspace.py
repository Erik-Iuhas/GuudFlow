"""Tests for core/git_workspace.py.

Covers: init idempotency, branch-per-role, commit on branch, isolation
between branches, diff, log, clean merge, conflict abort + signal emission.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.git_workspace import (
    GitCommitInfo,
    GitWorkspace,
    GitWorkspaceError,
)
from core.signals import SignalType


@pytest.fixture
def gw_emitter():
    """Returns (emitter_callable, captured_events_list)."""
    events: list[tuple[SignalType, str, str | None]] = []

    def emit(t: SignalType, summary: str, detail: str | None) -> None:
        events.append((t, summary, detail))

    return emit, events


@pytest.fixture
def gw(tmp_path: Path, gw_emitter):
    emit, _ = gw_emitter
    g = GitWorkspace(tmp_path / "working", emit=emit)
    g.init()
    return g


# ── Init ────────────────────────────────────────────────────────────────────


def test_init_creates_repo_with_main(tmp_path: Path):
    g = GitWorkspace(tmp_path / "working")
    g.init()
    assert (tmp_path / "working" / ".git").exists()
    assert g.repo.head.reference.name == "main"
    assert len(list(g.repo.iter_commits("main"))) == 1  # initial commit


def test_init_is_idempotent(tmp_path: Path):
    g = GitWorkspace(tmp_path / "working")
    g.init()
    g.init()
    assert len(list(g.repo.iter_commits("main"))) == 1


def test_repo_property_raises_before_init(tmp_path: Path):
    g = GitWorkspace(tmp_path / "working")
    with pytest.raises(GitWorkspaceError, match="not initialised"):
        _ = g.repo


# ── Branches ────────────────────────────────────────────────────────────────


def test_agent_branch_creates_lazy(gw: GitWorkspace):
    name = gw.agent_branch("developer")
    assert name == "agent/developer"
    assert "agent/developer" in [h.name for h in gw.repo.heads]


def test_agent_branch_idempotent(gw: GitWorkspace):
    gw.agent_branch("developer")
    gw.agent_branch("developer")
    assert sum(1 for h in gw.repo.heads if h.name == "agent/developer") == 1


def test_multiple_agent_branches(gw: GitWorkspace):
    gw.agent_branch("developer")
    gw.agent_branch("architect")
    names = {h.name for h in gw.repo.heads}
    assert {"agent/developer", "agent/architect"} <= names


# ── Commit ──────────────────────────────────────────────────────────────────


def test_commit_returns_info_and_emits_signal(gw: GitWorkspace, gw_emitter):
    _, events = gw_emitter
    (gw.working_dir / "auth.py").write_text("x = 1")
    info = gw.commit("developer", "feat: add auth module")
    assert isinstance(info, GitCommitInfo)
    assert info.branch == "agent/developer"
    assert info.message == "feat: add auth module"
    assert len(info.hash) >= 7
    types = [e[0] for e in events]
    assert SignalType.GIT_COMMIT in types


def test_commit_lands_on_agent_branch_not_main(gw: GitWorkspace):
    (gw.working_dir / "auth.py").write_text("x = 1")
    gw.commit("developer", "feat: x")
    main_commits = list(gw.repo.iter_commits("main"))
    dev_commits = list(gw.repo.iter_commits("agent/developer"))
    assert len(main_commits) == 1
    assert len(dev_commits) == 2


def test_commit_isolates_branches(gw: GitWorkspace):
    (gw.working_dir / "dev.py").write_text("d")
    gw.commit("developer", "feat: dev work")
    (gw.working_dir / "arch.md").write_text("a")
    gw.commit("architect", "feat: arch work")
    # architect branch should not contain dev.py contents
    assert gw.read_at("agent/architect", "arch.md") == "a"
    with pytest.raises(FileNotFoundError):
        gw.read_at("agent/architect", "dev.py")


def test_commit_specific_files_only(gw: GitWorkspace):
    (gw.working_dir / "a.py").write_text("a")
    (gw.working_dir / "b.py").write_text("b")
    gw.commit("developer", "feat: only a", files=["a.py"])
    # b.py was not staged → not on branch
    with pytest.raises(FileNotFoundError):
        gw.read_at("agent/developer", "b.py")
    assert gw.read_at("agent/developer", "a.py") == "a"


def test_commit_empty_message_raises(gw: GitWorkspace):
    (gw.working_dir / "x.py").write_text("x")
    with pytest.raises(ValueError, match="must not be empty"):
        gw.commit("developer", "")


def test_commit_with_no_changes_raises(gw: GitWorkspace):
    with pytest.raises(GitWorkspaceError, match="nothing staged"):
        gw.commit("developer", "feat: nothing")


# ── Read / diff / log ───────────────────────────────────────────────────────


def test_read_at_returns_branch_content(gw: GitWorkspace):
    (gw.working_dir / "auth.py").write_text("v1")
    gw.commit("developer", "feat: v1")
    assert gw.read_at("agent/developer", "auth.py") == "v1"


def test_read_at_missing_path_raises(gw: GitWorkspace):
    with pytest.raises(FileNotFoundError):
        gw.read_at("main", "nope.py")


def test_diff_shows_branch_changes(gw: GitWorkspace):
    (gw.working_dir / "auth.py").write_text("v1\n")
    gw.commit("developer", "feat: v1")
    diff = gw.diff("agent/developer", "main")
    assert "auth.py" in diff
    assert "v1" in diff


def test_log_returns_recent_commits(gw: GitWorkspace):
    (gw.working_dir / "a.py").write_text("a")
    gw.commit("developer", "feat: a")
    (gw.working_dir / "b.py").write_text("b")
    gw.commit("developer", "feat: b")
    log = gw.log("agent/developer", limit=5)
    messages = [e.message for e in log]
    assert "feat: b" in messages
    assert "feat: a" in messages


# ── Merge ───────────────────────────────────────────────────────────────────


def test_clean_merge_returns_true_and_emits(gw: GitWorkspace, gw_emitter):
    _, events = gw_emitter
    (gw.working_dir / "auth.py").write_text("v1")
    gw.commit("developer", "feat: auth")
    ok = gw.merge("agent/developer", into="main")
    assert ok is True
    assert any(e[0] == SignalType.GIT_MERGE for e in events)
    # main now has auth.py
    assert gw.read_at("main", "auth.py") == "v1"


def test_merge_conflict_returns_false_and_emits_conflict(
    gw: GitWorkspace, gw_emitter
):
    _, events = gw_emitter
    # Two branches both modify the same file in incompatible ways.
    (gw.working_dir / "shared.py").write_text("base\n")
    gw.commit("developer", "feat: shared base")
    gw.merge("agent/developer", into="main")  # main now has 'base'

    # branch A modifies
    gw.repo.git.checkout("main")
    branch_a = gw.agent_branch("alice")
    gw.repo.git.checkout(branch_a)
    (gw.working_dir / "shared.py").write_text("alice version\n")
    gw.commit("alice", "feat: alice changes")

    # branch B modifies same file differently
    gw.repo.git.checkout("main")
    branch_b = gw.agent_branch("bob")
    gw.repo.git.checkout(branch_b)
    (gw.working_dir / "shared.py").write_text("bob version\n")
    gw.commit("bob", "feat: bob changes")

    # Merge alice cleanly, then bob conflicts
    assert gw.merge(branch_a, into="main") is True
    events.clear()
    ok = gw.merge(branch_b, into="main")
    assert ok is False
    types = [e[0] for e in events]
    assert SignalType.GIT_CONFLICT in types
    # repo is in clean state (merge aborted)
    assert not gw.repo.git.status("--porcelain")


def test_no_emit_when_emitter_is_none(tmp_path: Path):
    g = GitWorkspace(tmp_path / "working", emit=None)
    g.init()
    (g.working_dir / "x.py").write_text("x")
    info = g.commit("developer", "feat: x")
    # Should not raise; commit succeeds without emitter.
    assert info.branch == "agent/developer"


# ── Phase G.5 — review-point tagging ───────────────────────────────────────


def test_tag_creates_annotated_tag(gw: GitWorkspace):
    """Tag at the current main HEAD; list_tags reflects it."""
    (gw.working_dir / "a.py").write_text("a")
    gw.commit("developer", "feat: a")
    gw.merge("agent/developer", into="main")

    assert gw.tag("review/1/reviewer", message="approved at iter 1") is True

    tags = gw.list_tags()
    assert len(tags) == 1
    assert tags[0]["name"] == "review/1/reviewer"
    assert tags[0]["message"].startswith("approved at iter 1")
    main_sha = gw.repo.heads.main.commit.hexsha
    assert tags[0]["sha"] == main_sha
    assert tags[0]["short_sha"] == main_sha[:7]


def test_tag_duplicate_returns_false(gw: GitWorkspace):
    (gw.working_dir / "a.py").write_text("a")
    gw.commit("developer", "feat: a")
    gw.merge("agent/developer", into="main")

    assert gw.tag("review/1/reviewer", message="x") is True
    assert gw.tag("review/1/reviewer", message="y") is False


def test_per_agent_author_name_on_commit(tmp_path: Path):
    """Commits carry the persona name, not the shared 'guudflow' author."""
    g = GitWorkspace(
        tmp_path / "working",
        hired_personas={"developer": "Sarah", "reviewer": "Mike"},
    )
    g.init()
    (g.working_dir / "dev.py").write_text("x")
    g.commit("developer", "feat: dev commit")
    (g.working_dir / "rev.py").write_text("y")
    g.commit("reviewer", "feat: reviewer commit")

    # With hired_personas set, branches use persona slug as prefix.
    dev_branch = g.repo.heads["sarah/developer"]
    rev_branch = g.repo.heads["mike/reviewer"]
    assert "Sarah" in dev_branch.commit.author.name
    assert "Mike" in rev_branch.commit.author.name
    # Emails are role-based, not shared
    assert dev_branch.commit.author.email == "developer@guudflow.local"
    assert rev_branch.commit.author.email == "reviewer@guudflow.local"


def test_commits_different_agents_have_distinct_authors(tmp_path: Path):
    """Two agents on separate branches have distinct author.name values."""
    g = GitWorkspace(
        tmp_path / "working",
        hired_personas={"agent_a": "Alice", "agent_b": "Bob"},
    )
    g.init()
    (g.working_dir / "a.txt").write_text("a")
    g.commit("agent_a", "feat: alice")
    (g.working_dir / "b.txt").write_text("b")
    g.commit("agent_b", "feat: bob")

    a_commit = g.repo.heads["alice/agent_a"].commit
    b_commit = g.repo.heads["bob/agent_b"].commit
    assert a_commit.author.name != b_commit.author.name


def test_list_tags_returns_in_chronological_order(gw: GitWorkspace):
    import time
    (gw.working_dir / "a.py").write_text("a")
    gw.commit("developer", "feat: a")
    gw.merge("agent/developer", into="main")
    gw.tag("review/1/reviewer", message="first")

    # Sleep so the tag timestamps differ — git's tagged_date has
    # second resolution.
    time.sleep(1.1)

    (gw.working_dir / "b.py").write_text("b")
    gw.commit("developer", "feat: b")
    gw.merge("agent/developer", into="main")
    gw.tag("review/2/reviewer", message="second")

    tags = gw.list_tags()
    assert [t["name"] for t in tags] == ["review/1/reviewer", "review/2/reviewer"]
