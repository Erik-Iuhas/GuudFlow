"""Track 4.4 — gitignore-aware file walker for FileWorkspace.list and repo_map."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.repo_walk import iter_repo_files


def _git(*args, cwd: Path):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo_with_gitignore(root: Path, ignore: str) -> None:
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "t@x.com", cwd=root)
    _git("config", "user.name", "T", cwd=root)
    (root / ".gitignore").write_text(ignore, encoding="utf-8")


def _write(root: Path, rel: str, content: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ── Git-repo path: respect .gitignore ──────────────────────────────────────


def test_iter_repo_files_respects_gitignore(tmp_path: Path):
    _init_repo_with_gitignore(tmp_path, "node_modules/\ndist/\n*.log\n")
    _write(tmp_path, "src/app.py")
    _write(tmp_path, "src/util.py")
    _write(tmp_path, "node_modules/lodash/index.js")
    _write(tmp_path, "dist/bundle.js")
    _write(tmp_path, "debug.log")

    files = iter_repo_files(tmp_path)
    assert "src/app.py" in files
    assert "src/util.py" in files
    assert ".gitignore" in files
    assert not any(f.startswith("node_modules/") for f in files)
    assert not any(f.startswith("dist/") for f in files)
    assert "debug.log" not in files


def test_iter_repo_files_includes_untracked_not_ignored(tmp_path: Path):
    """`--others` flag picks up brand-new files agents create, as long as
    they're not in .gitignore."""
    _init_repo_with_gitignore(tmp_path, "node_modules/\n")
    _write(tmp_path, "existing.py")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-q", "-m", "initial", cwd=tmp_path)

    # Brand-new file written after the commit — agents do this constantly.
    _write(tmp_path, "fresh_agent_output.md", "agent wrote this")

    files = iter_repo_files(tmp_path)
    assert "fresh_agent_output.md" in files
    assert "existing.py" in files


def test_iter_repo_files_subpath_filter(tmp_path: Path):
    _init_repo_with_gitignore(tmp_path, "")
    _write(tmp_path, "src/app.py")
    _write(tmp_path, "src/inner/helpers.py")
    _write(tmp_path, "tests/test_app.py")

    files = iter_repo_files(tmp_path, subpath="src")
    assert "src/app.py" in files
    assert "src/inner/helpers.py" in files
    assert "tests/test_app.py" not in files


def test_iter_repo_files_non_recursive_top_level_only(tmp_path: Path):
    _init_repo_with_gitignore(tmp_path, "")
    _write(tmp_path, "top.py")
    _write(tmp_path, "src/deep.py")

    files = iter_repo_files(tmp_path, recursive=False)
    assert "top.py" in files
    assert ".gitignore" in files
    assert "src/deep.py" not in files


# ── Non-git fallback ───────────────────────────────────────────────────────


def test_iter_repo_files_non_git_uses_skip_dirs(tmp_path: Path):
    """Outside a git repo, falls back to os.walk + the hardcoded skip set."""
    _write(tmp_path, "src/app.py")
    _write(tmp_path, "node_modules/lodash/index.js")
    _write(tmp_path, "__pycache__/cache.py")

    files = iter_repo_files(tmp_path)
    assert "src/app.py" in files
    assert not any(f.startswith("node_modules/") for f in files)
    assert not any(f.startswith("__pycache__/") for f in files)


def test_iter_repo_files_non_git_non_recursive(tmp_path: Path):
    _write(tmp_path, "top.py")
    _write(tmp_path, "src/deep.py")

    files = iter_repo_files(tmp_path, recursive=False)
    assert files == ["top.py"]


def test_iter_repo_files_missing_root(tmp_path: Path):
    files = iter_repo_files(tmp_path / "does_not_exist")
    assert files == []
