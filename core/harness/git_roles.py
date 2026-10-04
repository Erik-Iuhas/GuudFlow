"""Per-role git checkouts and runner-side branch checks (plan §5.5).

Layout per role (under `<run_root>/roles/<role>/`):

  work/      the role's worktree — the ONLY thing the agent can write
             (bind-mounted at /work in its sandbox). It has no `.git`.
  git/       the role's git directory, host-only, never mounted.
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
LOCAL_EXCLUDES = ("/.guudflow", "node_modules/", "__pycache__/")
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
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text("".join(f"{p}\n" for p in LOCAL_EXCLUDES))
        return work

    def sync_from_main(self, role: str) -> bool:
        """Merge the run's current `main` into the role's branch. False on conflict."""
        self.commit_leftovers(role, "chore: save work in progress before sync")
        self.git(role, "fetch", "--quiet", str(self.main.working_dir), "+main:refs/heads/main")
        try:
            self.git(role, "merge", "--quiet", "--no-edit", "main")
        except GitRoleError:
            self.git(role, "merge", "--abort")
            return False
        return True

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
        """Bring `agent/{role}` home: commit, fetch, check, merge into main."""
        branch = self.branch(role)
        first_line = summary.strip().splitlines()[0][:72] if summary.strip() else "update"
        self.commit_leftovers(role, f"chore({role}): {first_line}")
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
