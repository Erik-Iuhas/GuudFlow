"""Ensure GitWorkspace.merge() does not emit GIT_MERGE for no-op merges."""
from __future__ import annotations

from pathlib import Path

from core.git_workspace import GitWorkspace
from core.signals import SignalType


def _make_gw(tmp_path: Path):
    events: list[tuple[SignalType, str, str | None]] = []

    def emit(t: SignalType, summary: str, detail: str | None) -> None:
        events.append((t, summary, detail))

    gw = GitWorkspace(working_dir=tmp_path / "repo", emit=emit)
    gw.init()
    return gw, events


def test_noop_merge_no_signal(tmp_path: Path):
    """Merging a branch that is already an ancestor of main emits no GIT_MERGE."""
    gw, events = _make_gw(tmp_path)
    branch = gw.agent_branch("dev")
    # Write a file on dev and commit.
    f = tmp_path / "repo" / "hello.txt"
    f.write_text("hello")
    gw.commit("dev", "feat: hello", files=["hello.txt"])
    events.clear()

    # Merge dev → main once (real merge).
    gw.merge(branch, into="main")
    assert any(e[0] == SignalType.GIT_MERGE for e in events)
    events.clear()

    # Merge again — already up to date, should emit nothing.
    gw.merge(branch, into="main")
    assert not any(e[0] == SignalType.GIT_MERGE for e in events)


def test_real_merge_emits_signal(tmp_path: Path):
    """A merge that actually advances main does emit GIT_MERGE."""
    gw, events = _make_gw(tmp_path)
    branch = gw.agent_branch("dev")
    f = tmp_path / "repo" / "work.txt"
    f.write_text("work")
    gw.commit("dev", "feat: work", files=["work.txt"])
    events.clear()

    gw.merge(branch, into="main")
    assert any(e[0] == SignalType.GIT_MERGE for e in events)


def test_empty_branch_merge_no_signal(tmp_path: Path):
    """Merging a branch with no commits beyond main (empty branch) emits no GIT_MERGE."""
    gw, events = _make_gw(tmp_path)
    gw.agent_branch("empty_dev")
    events.clear()

    # agent/empty_dev is still at the initial commit — same as main.
    gw.merge("agent/empty_dev", into="main")
    assert not any(e[0] == SignalType.GIT_MERGE for e in events)
