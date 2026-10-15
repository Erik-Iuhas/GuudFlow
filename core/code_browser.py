"""The Code tab's read-only view of a project's code (plan §15).

Three sources, each a git repo plus what to show:

- `project`: the project repo's `main` (bare repo, nothing checked out);
- `run`: a run's `main` as turns merge into it, compared with the run's base;
- `role`: one role's worktree on disk, unmerged edits included, compared with the role's
  local `main` (its last sync).

Every git call is read-only (`--no-optional-locks`, hooks and fsmonitor off), protected paths
and paths outside the source are refused, binaries are not returned and files are capped.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.harness.artifacts import safe_resolve
from core.harness.git_roles import PROTECTED_PATHS, is_protected

MAX_ENTRIES = 5000
MAX_FILE_BYTES = 300_000
MAX_DIFF_CHARS = 200_000
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "--no-optional-locks")


class CodeBrowserError(RuntimeError):
    """A source or path that cannot be shown."""


@dataclass
class CodeEntry:
    """One file in a tree, with how the source changed it against its base."""

    path: str
    status: str = ""          # "" unchanged, "A" added, "M" modified, "D" deleted
    added: int = 0
    removed: int = 0


@dataclass
class CodeTree:
    """Every file of a source (deleted ones too, marked D)."""

    source: str
    entries: list[CodeEntry] = field(default_factory=list)
    base: str | None = None
    truncated: bool = False


@dataclass
class CodeFile:
    """One file's text, or why it is not shown, and optionally its diff against the base."""

    path: str
    text: str | None = None
    size: int = 0
    binary: bool = False
    too_large: bool = False
    deleted: bool = False
    diff: str | None = None


@dataclass(frozen=True)
class Source:
    """Where to read: a git invocation prefix, a ref (None: the worktree on disk) and a base."""

    kind: str
    git: tuple[str, ...]
    cwd: Path
    ref: str | None
    base: str | None
    worktree: Path | None = None

    @classmethod
    def project(cls, bare_repo: Path) -> "Source":
        """The accepted code: the project repo's `main`."""
        return cls("project", ("git", f"--git-dir={bare_repo}"), bare_repo, "main", None)

    @classmethod
    def run(cls, working_dir: Path, base: str | None) -> "Source":
        """A run's `main`, compared with the commit the run started from."""
        return cls("run", ("git", "-C", str(working_dir)), working_dir, "main", base)

    @classmethod
    def role(cls, git_dir: Path, worktree: Path) -> "Source":
        """A role's worktree on disk, compared with its local `main`."""
        return cls("role", ("git", f"--git-dir={git_dir}", f"--work-tree={worktree}"), git_dir,
                   None, "main", worktree)


def _git(src: Source, *args: str, raw: bool = False) -> bytes:
    cmd = [src.git[0], *_SAFE, *src.git[1:], *args]
    proc = subprocess.run(cmd, cwd=src.cwd, capture_output=True)
    if proc.returncode != 0:
        raise CodeBrowserError(proc.stderr.decode("utf-8", "replace").strip()[:300] or "git failed")
    return proc.stdout


def _paths(out: bytes) -> list[str]:
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def _excludes() -> list[str]:
    return [f":(exclude,glob){p}" for p in PROTECTED_PATHS]


def _changes(src: Source) -> dict[str, CodeEntry]:
    """What the source changed against its base: path -> entry (status and line counts)."""
    if src.base is None:
        return {}
    spec = ["--no-renames", src.base] + ([src.ref] if src.ref else []) + ["--", ".", *_excludes()]
    found: dict[str, CodeEntry] = {}
    status = _paths(_git(src, "diff", "--name-status", "-z", *spec))
    for kind, path in zip(status[0::2], status[1::2]):
        found[path] = CodeEntry(path, kind[:1])
    for line in _git(src, "diff", "--numstat", *spec).decode("utf-8", "replace").splitlines():
        added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
        if path in found:
            found[path].added = int(added) if added.isdigit() else 0
            found[path].removed = int(removed) if removed.isdigit() else 0
    return found


def tree(src: Source) -> CodeTree:
    """Every file in the source, protected paths left out, changes marked."""
    untracked: list[str] = []
    if src.ref is not None:
        names = _paths(_git(src, "ls-tree", "-r", "-z", "--name-only", src.ref))
    else:
        assert src.worktree is not None
        names = [p for p in _paths(_git(src, "ls-files", "-z")) if (src.worktree / p).exists()]
        untracked = _paths(_git(src, "ls-files", "-z", "--others", "--exclude-standard"))
        names += untracked
    changes = _changes(src)
    if src.worktree is not None:
        for p in untracked:
            if p not in changes:
                changes[p] = CodeEntry(p, "A", _line_count(src.worktree / p), 0)
    entries: dict[str, CodeEntry] = {}
    for p in names:
        if not is_protected(p):
            entries[p] = changes.get(p) or CodeEntry(p)
    for p, e in changes.items():
        if e.status == "D" and not is_protected(p):
            entries[p] = e
    ordered = sorted(entries.values(), key=lambda e: e.path)
    return CodeTree(src.kind, ordered[:MAX_ENTRIES], src.base, len(ordered) > MAX_ENTRIES)


def _line_count(path: Path) -> int:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            return 0
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def _clean(path: str) -> str:
    rel = path.strip()
    while rel.startswith("./"):
        rel = rel[2:]
    parts = Path(rel).parts
    if not rel or rel.startswith("/") or ".." in parts or "\\" in rel:
        raise CodeBrowserError(f"not a path inside the code: {path!r}")
    if is_protected(rel):
        raise CodeBrowserError(f"{rel} is a protected path")
    return rel


def read_file(src: Source, path: str, with_diff: bool = False) -> CodeFile:
    """One file's text (or why not), and its diff against the base when asked."""
    rel = _clean(path)
    out = CodeFile(rel)
    data: bytes | None = None
    if src.ref is not None:
        try:
            out.size = int(_git(src, "cat-file", "-s", f"{src.ref}:{rel}").strip() or 0)
        except CodeBrowserError:
            out.deleted = True
        if not out.deleted and out.size <= MAX_FILE_BYTES:
            data = _git(src, "cat-file", "blob", f"{src.ref}:{rel}")
    else:
        assert src.worktree is not None
        full = safe_resolve(src.worktree, rel)
        if full is None:
            raise CodeBrowserError(f"not a path inside the code: {path!r}")
        if not full.is_file():
            out.deleted = True
        else:
            out.size = full.stat().st_size
            if out.size <= MAX_FILE_BYTES:
                data = full.read_bytes()
    if not out.deleted:
        out.too_large = out.size > MAX_FILE_BYTES
        if data is not None:
            if b"\0" in data[:8192]:
                out.binary = True
            else:
                out.text = data.decode("utf-8", "replace")
    if with_diff and src.base is not None and not out.binary:
        out.diff = _diff(src, rel, out)
    return out


def _diff(src: Source, rel: str, f: CodeFile) -> str:
    spec = [src.base] + ([src.ref] if src.ref else [])
    text = _git(src, "diff", "--no-renames", "--no-ext-diff", "--no-textconv", *spec, "--",
                rel).decode("utf-8", "replace")
    if not text and src.ref is None and f.text is not None:
        tracked = _paths(_git(src, "ls-files", "-z", "--", rel))
        if not tracked:   # untracked new file: show it as added
            lines = f.text.splitlines()
            text = (f"diff --git a/{rel} b/{rel}\nnew file\n--- /dev/null\n+++ b/{rel}\n"
                    f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{ln}\n" for ln in lines))
    return text[:MAX_DIFF_CHARS]
