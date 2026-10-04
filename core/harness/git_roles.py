"""Per-role git clones and runner-side branch checks (plan §5.5).

Each role works in its own clone of the run repo, on `agent/{role}`. The agent
never sees the run repo, `main`, or another role's branch. After every turn the
runner (host side):

  1. commits anything the agent left uncommitted in its clone,
  2. fetches ONLY `agent/{role}` from the clone into the run repo,
  3. checks the branch: no protected paths touched, no obvious secrets,
  4. merges it into `main`.

Agents never merge. A failed check means the branch is not merged and the
turn is reported as failed with the reason.
"""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.git_workspace import GitWorkspace
from core.harness.capabilities import PROTECTED_PATHS
from core.secret_scan import _classify as classify_secret  # noqa: PLC2701 — shared rule set
from core.signals import SignalType

AGENT_BRANCH = "agent/{role}"
LOCAL_EXCLUDES = (".guudflow/",)


class GitRoleError(RuntimeError):
    """A git operation on a role clone failed."""


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitRoleError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()}")
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


class RoleClones:
    """Owns the per-role clones of one run's repo."""

    def __init__(self, main: GitWorkspace, clones_root: Path) -> None:
        self.main = main
        self.root = Path(clones_root)

    def branch(self, role: str) -> str:
        """The role's branch name."""
        return AGENT_BRANCH.format(role=role)

    def path(self, role: str) -> Path:
        """The role's clone directory."""
        return self.root / role

    def ensure(self, role: str, author: str) -> Path:
        """Create the role's clone (idempotent) on `agent/{role}` from main."""
        clone = self.path(role)
        if not (clone / ".git").exists():
            self.root.mkdir(parents=True, exist_ok=True)
            _git(self.root, "clone", "--quiet", "--no-hardlinks", "--branch", "main",
                 str(self.main.working_dir), role)
            _git(clone, "config", "user.name", f"{author} ({role})")
            _git(clone, "config", "user.email", f"{role}@guudflow.local")
            _git(clone, "config", "commit.gpgsign", "false")
            # The clone cannot push anywhere: the agent's only output is its branch,
            # which the runner fetches.
            _git(clone, "remote", "set-url", "--push", "origin", "no-push://disabled")
            _git(clone, "checkout", "--quiet", "-B", self.branch(role))
            exclude = clone / ".git" / "info" / "exclude"
            exclude.write_text(exclude.read_text() + "".join(f"{p}\n" for p in LOCAL_EXCLUDES)
                               if exclude.exists() else "".join(f"{p}\n" for p in LOCAL_EXCLUDES))
        return clone

    def sync_from_main(self, role: str) -> bool:
        """Merge the run's current `main` into the role's branch. False on conflict."""
        clone = self.path(role)
        self.commit_leftovers(role, "chore: save work in progress before sync")
        _git(clone, "fetch", "--quiet", str(self.main.working_dir), "main")
        try:
            _git(clone, "merge", "--quiet", "--no-edit", "FETCH_HEAD")
        except GitRoleError:
            _git(clone, "merge", "--abort")
            return False
        return True

    def commit_leftovers(self, role: str, message: str) -> str | None:
        """Commit any uncommitted changes in the clone; returns the new sha or None."""
        clone = self.path(role)
        if not _git(clone, "status", "--porcelain"):
            return None
        _git(clone, "add", "-A")
        _git(clone, "commit", "--quiet", "-m", message)
        return _git(clone, "rev-parse", "HEAD")

    def collect(self, role: str, summary: str) -> CollectResult:
        """Bring `agent/{role}` home: commit leftovers, fetch, check, merge into main."""
        branch = self.branch(role)
        clone = self.path(role)
        first_line = summary.strip().splitlines()[0][:72] if summary.strip() else "update"
        self.commit_leftovers(role, f"chore({role}): {first_line}")
        repo = self.main.repo
        repo.git.checkout("main")
        before = repo.git.rev_parse("main")
        # Fetch ONLY the role's branch; nothing else in the clone can reach the run repo.
        repo.git.fetch(str(clone), f"+{branch}:refs/heads/{branch}")
        new_commits = [c for c in repo.git.rev_list(f"main..{branch}").split() if c]
        if not new_commits:
            return CollectResult(merged=True)
        changed = [p for p in repo.git.diff("--name-only", f"{before}...{branch}").splitlines() if p]
        protected = [p for p in changed if is_protected(p)]
        if protected:
            return CollectResult(merged=False, new_commits=new_commits, changed_files=changed,
                                 problem=f"touched protected paths: {', '.join(protected)}")
        secrets = [f"{p} ({reason})" for p in changed
                   if (clone / p).is_file() and (reason := classify_secret(clone / p, p))]
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
        return CollectResult(merged=True, new_commits=new_commits, changed_files=changed)
