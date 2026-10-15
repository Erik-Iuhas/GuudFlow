"""Per-role git checkouts and runner-side branch checks (plan §5.5).

Layout per role (under `<run_root>/roles/<role>/`):

  work/      the role's worktree — the ONLY thing the agent can write
             (bind-mounted at /work in its sandbox). Its `.git` is only a boundary:
             a file pointing at `boundary.git`, never at `git/`.
  git/       the role's git directory, host-only, never mounted.
  boundary.git/  an empty bare repo. Without a `.git` in `work/`, opencode (and the
             agent's own git commands) walk up to the nearest enclosing repo; in host
             mode the run lives inside GuudFlow's checkout, so that repo (its `.env`,
             `data/secrets/`) became the agent's project and readable. Host git never
             uses it: every host call names `git/` and `work/` explicitly.
  guudflow/  host-written tool output (screenshots, installed packages,
             diffs), mounted read-only at /work/.guudflow in the sandbox.

Why the git directory is separate (O8 review): if the agent could write its own
`.git`, a planted `core.fsmonitor`, hook path or clean filter would make the
host execute agent-chosen commands the next time the runner ran `git add`.
Here every host-side git call names the host-owned git dir explicitly and turns
hooks and fsmonitor off, so nothing in the worktree can configure git.

After every turn the runner (host side):
  1. commits the worktree on `agent/{role}`,
  2. fetches ONLY that branch into the run repo,
  3. checks it: no protected paths touched, no obvious secrets,
  4. merges it into `main`.
Agents never merge. A failed check means the branch is not merged and the turn
is reported as failed with the reason.

Keeping the flow moving (no dead ends):
  - A branch that fails a check is reset to `main` and its patch is saved under
    the role's tool dir (`.guudflow/set_aside/`), so a rejected commit can never
    block every later turn of that role.
  - A merge conflict is not a failure on its own: `start_conflict_resolution`
    merges `main` into the role's branch and leaves the conflict markers in the
    worktree for the agent to resolve in the same session; `collect` refuses
    files that still carry markers.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.git_workspace import GitWorkspace
from core.harness.capabilities import PROTECTED_PATHS
from core.secret_scan import _classify as classify_secret  # noqa: PLC2701 — shared rule set
from core.signals import SignalType

AGENT_BRANCH = "agent/{role}"
# ".guudflow" without a slash also matches the host-mode symlink.
LOCAL_EXCLUDES = ("/.guudflow", "/node_modules", "node_modules/", "__pycache__/")
_SAFE_GIT = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "commit.gpgsign=false", "-c", "core.symlinks=true")


class GitRoleError(RuntimeError):
    """A git operation on a role checkout failed."""


def _run(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitRoleError(f"{' '.join(args[:6])}… failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def is_protected(path: str, patterns: tuple[str, ...] = PROTECTED_PATHS) -> bool:
    """True if a repo-relative path matches a protected pattern."""
    return any(fnmatch.fnmatch(path, p) or path == p.rstrip("/*") for p in patterns)


@dataclass
class CollectResult:
    """Outcome of bringing a role's branch home."""

    merged: bool
    new_commits: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    problem: str | None = None
    base: str = ""      # main before the merge
    head: str = ""      # main after the merge (base..head is exactly what was merged)
    conflict: bool = False          # main moved and the merge conflicted (resolvable)
    set_aside: str | None = None    # tool-dir-relative patch of discarded commits


@dataclass
class ChangedFile:
    """One file in a role's uncommitted-or-unmerged work."""

    path: str
    status: str         # "M" | "A" | "D" | "untracked"
    added: int
    removed: int


@dataclass
class WorkingChanges:
    """A role's work in progress: its worktree against its local `main`."""

    base: str
    head: str
    files: list[ChangedFile]
    diff: str
    truncated: bool


_UNTRACKED_MAX_BYTES = 200_000


def _new_file_patch(full: Path, path: str) -> tuple[str, int]:
    """A unified new-file diff for an untracked file and its line count; ("", 0) for
    symlinks, binaries and big files."""
    try:
        if full.is_symlink() or not full.is_file() or full.stat().st_size > _UNTRACKED_MAX_BYTES:
            return "", 0
        data = full.read_bytes()
    except OSError:
        return "", 0
    if b"\0" in data:
        return "", 0
    lines = data.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return "", 0
    body = "\n".join(f"+{line}" for line in lines)
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
            f"@@ -0,0 +1,{len(lines)} @@\n{body}\n"), len(lines)


class RoleClones:
    """Owns the per-role checkouts of one run's repo."""

    def __init__(self, main: GitWorkspace, clones_root: Path) -> None:
        self.main = main
        self.root = Path(clones_root)

    def branch(self, role: str) -> str:
        """The role's branch name."""
        return AGENT_BRANCH.format(role=role)

    def path(self, role: str) -> Path:
        """The role's worktree (what the agent sees as /work)."""
        return self.root / role / "work"

    def git_dir(self, role: str) -> Path:
        """The role's host-only git directory."""
        return self.root / role / "git"

    def tool_dir(self, role: str) -> Path:
        """Host-written tool output for the role (/work/.guudflow, read-only to the agent)."""
        return self.root / role / "guudflow"

    def git(self, role: str, *args: str) -> str:
        """Run git for `role` against its host-owned git dir and worktree."""
        return _run(["git", f"--git-dir={self.git_dir(role)}", f"--work-tree={self.path(role)}",
                     *_SAFE_GIT, *args], cwd=self.git_dir(role))

    def main_numstat(self, base: str, head: str) -> list[tuple[str, int, int]]:
        """(path, added, removed) for each file `head` changed on main since `base`."""
        out = _run(["git", *_SAFE_GIT, "diff", "--numstat", base, head], cwd=self.main.working_dir)
        rows = []
        for line in out.splitlines():
            added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
            if path:
                rows.append((path, int(added) if added.isdigit() else 0,
                             int(removed) if removed.isdigit() else 0))
        return rows

    def main_file(self, rev: str, path: str) -> str:
        """A file's text on main at `rev` ("" if it does not exist there)."""
        try:
            return _run(["git", *_SAFE_GIT, "show", f"{rev}:{path}"], cwd=self.main.working_dir)
        except GitRoleError:
            return ""

    def ensure(self, role: str, author: str) -> Path:
        """Create the role's checkout (idempotent) on `agent/{role}` from main."""
        work, gdir = self.path(role), self.git_dir(role)
        if not (gdir / "HEAD").exists():
            work.mkdir(parents=True, exist_ok=True)
            self.tool_dir(role).mkdir(parents=True, exist_ok=True)
            _run(["git", "init", "--quiet", "--bare", str(gdir)], cwd=self.root)
            self.git(role, "config", "core.bare", "false")
            self.git(role, "config", "user.name", f"{author} ({role})")
            self.git(role, "config", "user.email", f"{role}@guudflow.local")
            self.git(role, "fetch", "--quiet", str(self.main.working_dir), "main:refs/heads/main")
            self.git(role, "checkout", "--quiet", "-B", self.branch(role), "main")
        exclude = gdir / "info" / "exclude"
        wanted = "".join(f"{p}\n" for p in LOCAL_EXCLUDES)
        if not exclude.is_file() or exclude.read_text() != wanted:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(wanted)
        boundary = self.root / role / "boundary.git"
        if not (boundary / "HEAD").exists():
            _run(["git", "init", "--quiet", "--bare", str(boundary)], cwd=self.root)
        (work / ".git").write_text("gitdir: ../boundary.git\n")
        return work

    def sync_from_main(self, role: str, keep_conflicts: bool = False) -> bool:
        """Merge the run's current `main` into the role's branch. False on conflict.

        With `keep_conflicts` the merge stays in progress and the conflicted files
        keep their markers for the agent to resolve (see `conflicted_files`).
        """
        if self.merging(role):
            if self.conflicted_files(role):
                return False
            self.commit_leftovers(role, "merge: main into the branch (conflicts resolved)")
        self.commit_leftovers(role, "chore: save work in progress before sync")
        self.git(role, "fetch", "--quiet", str(self.main.working_dir), "+main:refs/heads/main")
        try:
            self.git(role, "merge", "--quiet", "--no-edit", "main")
        except GitRoleError:
            if not keep_conflicts or not self.conflicted_files(role):
                self.git(role, "merge", "--abort")
                return False
            return False
        return True

    def merging(self, role: str) -> bool:
        """Is a merge of main in progress in the role's checkout?"""
        return (self.git_dir(role) / "MERGE_HEAD").exists()

    def conflicted_files(self, role: str) -> list[str]:
        """Files of an in-progress merge that still carry conflict markers.

        The agent has no git, so the index never records its resolution: a file
        counts as resolved once its markers are gone (or it was deleted).
        """
        if not self.merging(role):
            return []
        unmerged = self.git(role, "diff", "--name-only", "--diff-filter=U").splitlines()
        return sorted(p for p in set(unmerged) if p and _has_markers(self.path(role) / p))

    def start_conflict_resolution(self, role: str) -> list[str]:
        """Merge main into the role's branch, keeping conflict markers for the agent.

        Returns the conflicted files ([] when the merge went through cleanly).
        """
        self.sync_from_main(role, keep_conflicts=True)
        return self.conflicted_files(role)

    def abort_conflict_resolution(self, role: str) -> None:
        """Give up on a conflict: back to the branch as it was before the merge."""
        if self.merging(role):
            self.git(role, "merge", "--abort")

    def set_aside(self, role: str, reason: str) -> str | None:
        """Reset the role's branch to main; save what was dropped as a patch.

        Returns the patch path relative to the tool dir, or None if nothing was dropped.
        """
        self.abort_conflict_resolution(role)
        self.commit_leftovers(role, "chore: work set aside")
        self.git(role, "fetch", "--quiet", str(self.main.working_dir), "+main:refs/heads/main")
        patch = self.git(role, "diff", "main", "HEAD")
        rel = None
        if patch.strip():
            folder = self.tool_dir(role) / "set_aside"
            folder.mkdir(parents=True, exist_ok=True)
            n = len(list(folder.glob("*.patch"))) + 1
            rel = f"set_aside/{n:02d}.patch"
            (self.tool_dir(role) / rel).write_text(f"# set aside: {reason}\n{patch}\n",
                                                   encoding="utf-8")
        self.git(role, "reset", "--quiet", "--hard", "main")
        self.git(role, "clean", "-fdq")
        return rel

    def commit_leftovers(self, role: str, message: str) -> str | None:
        """Commit any changes in the worktree; returns the new sha or None."""
        if not self.git(role, "status", "--porcelain"):
            return None
        self.git(role, "add", "-A")
        self.git(role, "commit", "--quiet", "-m", message)
        return self.git(role, "rev-parse", "HEAD")

    def changes(self, role: str, base: str = "main", path: str | None = None,
                limit: int = 30_000) -> str:
        """`git diff --stat` plus the patch of the worktree against `base` (for review)."""
        self.commit_leftovers(role, "chore: snapshot for review")
        spec = [base, "--"] + ([path] if path else [])
        stat = self.git(role, "diff", "--stat", *spec)
        patch = self.git(role, "diff", *spec)
        text = f"{stat}\n\n{patch}" if stat else "(no changes)"
        return text if len(text) <= limit else text[:limit] + "\n[truncated]"

    def history(self, role: str, limit: int = 20) -> str:
        """Recent commits on the role's branch."""
        return self.git(role, "log", f"-{max(1, min(limit, 100))}", "--format=%h %an: %s")

    def working_changes(self, role: str, limit: int = 200_000) -> WorkingChanges:
        """The role's worktree against its local `main` (last synced at spawn), read-only.

        Unlike `changes`, nothing is committed, fetched or locked (`--no-optional-locks`),
        so Studio can poll it while the agent works. Protected paths are left out;
        untracked text files are rendered as new-file diffs, symlinks and binaries are not read.
        """
        ro = ("--no-optional-locks",)
        excludes = [f":(exclude,glob){p}" for p in PROTECTED_PATHS]
        spec = ["--no-renames", "--no-ext-diff", "--no-textconv", "main", "--", ".", *excludes]
        base = self.git(role, *ro, "rev-parse", "main")
        head = self.git(role, *ro, "rev-parse", "HEAD")
        counts: dict[str, tuple[int, int]] = {}
        for line in self.git(role, *ro, "diff", "--numstat", *spec).splitlines():
            added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
            counts[path] = (int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0)
        files: list[ChangedFile] = []
        for line in self.git(role, *ro, "diff", "--name-status", *spec).splitlines():
            status, _, path = line.partition("\t")
            added, removed = counts.get(path, (0, 0))
            files.append(ChangedFile(path=path, status=status[:1], added=added, removed=removed))
        parts = [self.git(role, *ro, "diff", *spec)]
        work = self.path(role)
        for path in self.git(role, *ro, "ls-files", "--others", "--exclude-standard").splitlines():
            if is_protected(path):
                continue
            body, lines = _new_file_patch(work / path, path)
            files.append(ChangedFile(path=path, status="untracked", added=lines, removed=0))
            if body:
                parts.append(body)
        diff = "\n".join(p for p in parts if p)
        truncated = len(diff) > limit
        return WorkingChanges(base=base, head=head, files=files,
                              diff=diff[:limit] if truncated else diff, truncated=truncated)

    def merged_by_role(self, role: str, base: str, limit: int = 200_000) -> WorkingChanges:
        """What `role` has merged into the run's main since `base` (the run's start), read-only.

        The role's own commits (author email `{role}@guudflow.local`, no merge commits), oldest
        first, each as a patch under a `# <sha> <subject>` line; files sum their line counts.
        Studio shows this once the role's turns are merged and its worktree is clean again.
        """
        cwd = self.main.working_dir
        ro = ("--no-optional-locks",)
        excludes = [f":(exclude,glob){p}" for p in PROTECTED_PATHS]
        head = _run(["git", *_SAFE_GIT, *ro, "rev-parse", "main"], cwd=cwd)
        shas = _run(["git", *_SAFE_GIT, *ro, "log", "--no-merges", "--reverse", "--format=%H",
                     f"--author=<{role}@guudflow.local>", f"{base}..main"], cwd=cwd).split()
        counts: dict[str, list[int]] = {}
        parts: list[str] = []
        for sha in shas:
            for line in _run(["git", *_SAFE_GIT, *ro, "show", "--numstat", "--format=", "--no-renames",
                              sha, "--", ".", *excludes], cwd=cwd).splitlines():
                added, removed, path = (line.split("\t", 2) + ["", "", ""])[:3]
                if path:
                    c = counts.setdefault(path, [0, 0])
                    c[0] += int(added) if added.isdigit() else 0
                    c[1] += int(removed) if removed.isdigit() else 0
            parts.append(_run(["git", *_SAFE_GIT, *ro, "show", "--format=# %h %s", "--no-renames",
                               "--no-ext-diff", "--no-textconv", sha, "--", ".", *excludes], cwd=cwd))
        files = [ChangedFile(path=p, status="M", added=a, removed=r) for p, (a, r) in counts.items()]
        diff = "\n\n".join(parts)
        truncated = len(diff) > limit
        return WorkingChanges(base=base, head=head, files=files,
                              diff=diff[:limit] if truncated else diff, truncated=truncated)

    def _unsafe_symlinks(self, branch: str, paths: list[str]) -> list[str]:
        """Committed symlinks on `branch` that are absolute or climb out of the repo.

        Such a link merged into main would make host-side readers (repo map,
        artifact scan, a later checkout) follow it to host files.
        """
        if not paths:
            return []
        repo, bad = self.main.repo, []
        for line in repo.git.ls_tree("-r", branch, "--", *paths).splitlines():
            meta, _, path = line.partition("\t")
            mode, _kind, sha = meta.split()
            if mode != "120000":
                continue
            target = repo.git.cat_file("-p", sha)
            resolved = os.path.normpath(os.path.join("/repo", os.path.dirname(path), target))
            if target.startswith("/") or not resolved.startswith("/repo/"):
                bad.append(f"{path} -> {target}")
        return bad

    def collect(self, role: str, summary: str) -> CollectResult:
        """Bring `agent/{role}` home: commit, fetch, check, merge into main.

        A failed check sets the branch aside (reset to main, patch kept). A merge
        conflict leaves the branch as is (`conflict=True`) so the caller can have
        the agent resolve it; see `start_conflict_resolution`.
        """
        branch = self.branch(role)
        first_line = summary.strip().splitlines()[0][:72] if summary.strip() else "update"
        markers = self.conflicted_files(role)
        if markers:
            return CollectResult(merged=False, conflict=True, changed_files=markers,
                                 problem=f"unresolved conflict markers in: {', '.join(markers)}")
        self.commit_leftovers(role, f"chore({role}): {first_line}")
        result = self._collect(role, branch)
        if not result.merged and not result.conflict and result.problem:
            result.set_aside = self.set_aside(role, result.problem)
        return result

    def _collect(self, role: str, branch: str) -> CollectResult:
        repo = self.main.repo
        repo.git.checkout("main")
        before = repo.git.rev_parse("main")
        # Fetch ONLY the role's branch from the host-owned git dir.
        repo.git.fetch(str(self.git_dir(role)), f"+{branch}:refs/heads/{branch}")
        new_commits = [c for c in repo.git.rev_list(f"main..{branch}").split() if c]
        if not new_commits:
            return CollectResult(merged=True)
        changed = [p for p in repo.git.diff("--name-only", f"{before}...{branch}").splitlines() if p]
        protected = [p for p in changed if is_protected(p)]
        if protected:
            return CollectResult(merged=False, new_commits=new_commits, changed_files=changed,
                                 problem=f"touched protected paths: {', '.join(protected)}")
        links = self._unsafe_symlinks(branch, changed)
        if links:
            return CollectResult(merged=False, new_commits=new_commits, changed_files=changed,
                                 problem=f"symlinks pointing outside the repo: {', '.join(links)}")
        work = self.path(role)
        marked = [p for p in changed if (work / p).is_file() and not (work / p).is_symlink()
                  and _has_markers(work / p)]
        if marked:
            return CollectResult(merged=False, new_commits=new_commits, changed_files=changed,
                                 problem=f"unresolved conflict markers in: {', '.join(marked)}")
        secrets = [f"{p} ({reason})" for p in changed
                   if (work / p).is_file() and not (work / p).is_symlink()
                   and (reason := classify_secret(work / p, p))]
        if secrets:
            return CollectResult(merged=False, new_commits=new_commits, changed_files=changed,
                                 problem=f"possible secrets committed: {', '.join(secrets)}")
        if self.main.emit is not None:
            subject = repo.git.log("-1", "--format=%s", branch)
            self.main.emit(SignalType.GIT_COMMIT,
                           f"{role}: {len(new_commits)} commit(s), {len(changed)} file(s) — {subject}",
                           "\n".join(changed))
        if not self.main.merge(branch, into="main"):
            return CollectResult(merged=False, new_commits=new_commits, changed_files=changed,
                                 conflict=True,
                                 problem=f"merge conflict merging {branch} into main")
        return CollectResult(merged=True, new_commits=new_commits, changed_files=changed,
                             base=before, head=repo.git.rev_parse("main"))

    def merged_diff(self, ranges: list[tuple[str, str]], limit: int = 30_000) -> str:
        """Stat + patch of what the given (base, head) merges brought into main."""
        repo = self.main.repo
        parts = []
        for base, head in ranges:
            stat = repo.git.diff("--stat", base, head)
            parts.append(f"# {base[:8]}..{head[:8]}\n{stat}\n\n{repo.git.diff(base, head)}")
        text = "\n\n".join(parts) if parts else "(nothing merged for this card yet)"
        return text if len(text) <= limit else text[:limit] + "\n[truncated]"


_MARKER_PREFIXES = ("<<<<<<< ", ">>>>>>> ")


def _has_markers(path: Path) -> bool:
    """True if a text file still has git conflict markers (`<<<<<<< ` / `>>>>>>> ` lines)."""
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
            return False
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(line.startswith(_MARKER_PREFIXES) for line in text.splitlines())
