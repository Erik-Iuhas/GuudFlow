"""Shared repo walker — respects .gitignore when the tree is a git repo.

Track 4.4. AgentFlow's `FileWorkspace.list`, `repo_map`, and any other
component that needs to enumerate "the files that matter in this tree"
should route through `iter_repo_files()` here. The result honors
`.gitignore`, `.git/info/exclude`, and the global excludes file — exactly
what the user expects when AgentFlow is pointed at a real repo (Track
4.3 in-place mode).

Mechanism:
- If `<root>/.git` exists, shell out to `git ls-files --cached --others
  --exclude-standard` and return its output. One subprocess, ~30ms on a
  10k-file repo.
- Otherwise fall back to a manual walk that prunes a small fixed list of
  vendored / build / cache directories — enough to keep new sandbox
  workspaces fast without requiring a git init.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable


# Hard-coded fallback skip set used only when we're NOT inside a git repo
# (so .gitignore can't steer the walk). Mirrors the most common offenders
# that explode tree-sitter / file listings on real-world projects.
_FALLBACK_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules",
    "venv", "env", ".venv", ".env",
    "dist", "build", "out", "target",
    "__pycache__",
    ".flow",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "vendor",
    "coverage", "htmlcov", ".nyc_output",
    ".next", ".nuxt", ".svelte-kit", ".turbo",
    "bower_components",
    ".tox",
    "site-packages",
})


def _is_git_repo(root: Path) -> bool:
    """True iff `<root>/.git` exists (worktree or full repo)."""
    return (root / ".git").exists()


def _git_ls_files(root: Path, subpath: str = "") -> list[str] | None:
    """Run `git ls-files --cached --others --exclude-standard` rooted at
    `root`, optionally restricted to `subpath`. Returns repo-relative
    posix paths or None if git is unavailable / fails.

    Listing tracked + untracked-but-not-ignored gives us exactly the
    files an agent should reason about, including new files they create.
    """
    args = ["git", "-C", str(root), "ls-files",
            "--cached", "--others", "--exclude-standard"]
    if subpath:
        args.append(subpath)
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [line for line in out.stdout.splitlines() if line]


def _walk_with_skip(
    root: Path, recursive: bool, subpath: str,
) -> list[str]:
    """Manual fallback when not in a git repo. Prunes _FALLBACK_SKIP_DIRS
    so a sandbox `node_modules` (rare but possible) doesn't tank the
    walk."""
    base = (root / subpath) if subpath else root
    if not base.is_dir():
        return []
    out: list[str] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in _FALLBACK_SKIP_DIRS
            ]
            for fname in filenames:
                full = Path(dirpath) / fname
                rel = full.relative_to(root).as_posix()
                out.append(rel)
    else:
        for entry in base.iterdir():
            if entry.is_file():
                out.append(entry.relative_to(root).as_posix())
    out.sort()
    return out


def iter_repo_files(
    root: Path | str,
    *,
    subpath: str = "",
    recursive: bool = True,
) -> list[str]:
    """Return repo-relative posix paths under `<root>/<subpath>`.

    - In a git repo: respects .gitignore via `git ls-files`. `recursive`
      is implicit (git always returns the full set); the `recursive=False`
      caller can post-filter with `'/' not in rel_minus_subpath`.
    - Outside a git repo: falls back to `os.walk` with the fixed skip set.

    The non-recursive case is honored explicitly so existing callers of
    `FileWorkspace.list(path, recursive=False)` keep their behavior.
    """
    root = Path(root).resolve()
    sub = subpath.strip("/\\")
    if _is_git_repo(root):
        files = _git_ls_files(root, sub)
        if files is None:
            # git failed unexpectedly — fall back to manual walk.
            return _walk_with_skip(root, recursive, sub)
        if not recursive:
            # Limit to the top level of `subpath` (or root).
            prefix = (sub + "/") if sub else ""
            depth_pruned = []
            for f in files:
                if not f.startswith(prefix):
                    continue
                rest = f[len(prefix):]
                if "/" not in rest:
                    depth_pruned.append(f)
            return sorted(depth_pruned)
        if sub:
            prefix = sub + "/"
            files = [f for f in files if f.startswith(prefix) or f == sub]
        return sorted(files)
    return _walk_with_skip(root, recursive, sub)
