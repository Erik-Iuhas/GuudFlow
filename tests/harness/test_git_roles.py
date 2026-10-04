"""Tests for core.harness.git_roles (plan §5.5)."""

from __future__ import annotations

from pathlib import Path

from core.git_workspace import GitWorkspace
from core.harness.git_roles import RoleClones, is_protected
from core.signals import SignalType


def _setup(tmp_path: Path) -> tuple[GitWorkspace, RoleClones, list[tuple[SignalType, str]]]:
    signals: list[tuple[SignalType, str]] = []
    main = GitWorkspace(tmp_path / "working", emit=lambda t, s, d: signals.append((t, s)))
    main.init()
    return main, RoleClones(main, tmp_path / "roles"), signals


def test_is_protected() -> None:
    assert is_protected("inputs/spec.md")
    assert is_protected("opencode.json")
    assert is_protected(".opencode/agent/x.md")
    assert is_protected(".github/workflows/ci.yml")
    assert not is_protected("src/app.py")
    assert not is_protected("docs/inputs.md")


def test_worktree_has_no_git_dir_and_branch_lives_host_side(tmp_path: Path) -> None:
    _, clones, _ = _setup(tmp_path)
    work = clones.ensure("builder", "Sam")
    assert not (work / ".git").exists()
    assert clones.git("builder", "rev-parse", "--abbrev-ref", "HEAD") == "agent/builder"
    assert clones.git_dir("builder").is_dir() and clones.tool_dir("builder").is_dir()
    assert not clones.git_dir("builder").is_relative_to(work)


def test_planted_git_config_hooks_and_filters_never_run_on_host(tmp_path: Path) -> None:
    """O8 finding: agent-written git config must not make the host execute commands."""
    _, clones, _ = _setup(tmp_path)
    work = clones.ensure("builder", "Sam")
    pwn = tmp_path / "pwned"
    fake_git = work / ".git"
    (fake_git / "hooks").mkdir(parents=True)
    (fake_git / "config").write_text(
        f"[core]\n\tfsmonitor = touch {pwn}-fsmonitor\n\thooksPath = .git/hooks\n"
        f"[filter \"evil\"]\n\tclean = touch {pwn}-filter\n")
    hook = fake_git / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {pwn}-hook\n")
    hook.chmod(0o755)
    (work / ".gitattributes").write_text("* filter=evil\n")
    (work / "app.py").write_text("x = 1\n")
    result = clones.collect("builder", "add app")
    assert result.merged
    assert not list(tmp_path.glob("pwned*")), "nothing planted in the worktree may run"


def test_collect_commits_leftovers_and_merges(tmp_path: Path) -> None:
    main, clones, signals = _setup(tmp_path)
    clone = clones.ensure("builder", "Sam")
    (clone / "app.py").write_text("print('hi')\n")
    result = clones.collect("builder", "Add app entry point")
    assert result.merged and result.changed_files == ["app.py"] and result.new_commits
    assert (main.working_dir / "app.py").read_text() == "print('hi')\n"
    kinds = [t for t, _ in signals]
    assert SignalType.GIT_COMMIT in kinds and SignalType.GIT_MERGE in kinds


def test_collect_without_changes_is_a_clean_noop(tmp_path: Path) -> None:
    _, clones, signals = _setup(tmp_path)
    clones.ensure("reviewer", "Rita")
    result = clones.collect("reviewer", "Reviewed, nothing to change")
    assert result.merged and not result.new_commits and not signals


def test_protected_path_blocks_merge(tmp_path: Path) -> None:
    main, clones, _ = _setup(tmp_path)
    clone = clones.ensure("builder", "Sam")
    (clone / "opencode.json").write_text('{"permission": "allow"}')
    (clone / "ok.py").write_text("x = 1\n")
    result = clones.collect("builder", "sneaky")
    assert not result.merged and "opencode.json" in (result.problem or "")
    assert not (main.working_dir / "opencode.json").exists()
    assert not (main.working_dir / "ok.py").exists()


def test_secret_blocks_merge(tmp_path: Path) -> None:
    main, clones, _ = _setup(tmp_path)
    clone = clones.ensure("builder", "Sam")
    (clone / "deploy.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n")
    result = clones.collect("builder", "add key")
    assert not result.merged and "deploy.pem" in (result.problem or "")


def test_changes_and_history_for_review(tmp_path: Path) -> None:
    _, clones, _ = _setup(tmp_path)
    work = clones.ensure("builder", "Sam")
    (work / "a.py").write_text("print(1)\n")
    text = clones.changes("builder")
    assert "a.py" in text and "+print(1)" in text
    assert "snapshot for review" in clones.history("builder")


def test_sync_brings_upstream_work_into_role_branch(tmp_path: Path) -> None:
    _, clones, _ = _setup(tmp_path)
    a = clones.ensure("architect", "Ann")
    (a / "design.md").write_text("# design\n")
    assert clones.collect("architect", "design").merged
    b = clones.ensure("builder", "Sam")
    assert clones.sync_from_main("builder")
    assert (b / "design.md").exists()


def test_parallel_conflict_is_reported(tmp_path: Path) -> None:
    _, clones, _ = _setup(tmp_path)
    a, b = clones.ensure("a", "A"), clones.ensure("b", "B")
    (a / "same.py").write_text("a\n")
    (b / "same.py").write_text("b\n")
    assert clones.collect("a", "a").merged
    result = clones.collect("b", "b")
    assert not result.merged and "conflict" in (result.problem or "")
