"""GitWorkspace — git operations on the run's `working/` directory.

Every run initialises a git repo inside `workspace/working/`. Each agent
role gets a dedicated `agent/{role}` branch. Agents only write to their own
branch but can read any branch. Reviewers (or the runner) merge approved
branches into main; rejected work stays on the branch for the next iteration.

DESIGN §19 lays out the full model. This module wraps gitpython and emits
GIT_COMMIT / GIT_MERGE / GIT_CONFLICT signals via an injected callback so it
stays independent of the Workspace dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import git
from git import GitCommandError, Repo

from core.signals import SignalType


SignalEmitter = Callable[[SignalType, str, str | None], None]
"""Callback signature: (signal_type, summary, detail). Detail is None for
short events like a single commit; populated for things like merge conflicts."""


def _diff_state(d) -> str:
    """Map a gitpython Diff change_type letter to a human-readable state."""
    code = d.change_type if hasattr(d, "change_type") else "M"
    return {
        "A": "added",
        "D": "deleted",
        "M": "modified",
        "R": "renamed",
        "T": "modified",
        "U": "unmerged",
    }.get(code, "modified")


class GitWorkspaceError(RuntimeError):
    """Raised on git operations that have no recoverable path."""


@dataclass
class GitCommitInfo:
    """What `commit()` returns. The runner combines this with workspace
    state (iteration, role) to build a workspace.git CommitRecord."""

    hash: str
    branch: str
    message: str
    timestamp: datetime


@dataclass
class GitLogEntry:
    hash: str
    branch: str
    author: str
    message: str
    timestamp: datetime


@dataclass
class GitStatusEntry:
    """One uncommitted change in the working tree."""

    path: str
    state: str  # "modified" | "added" | "deleted" | "renamed" | "untracked"
    staged: bool


@dataclass
class GitStatusResult:
    branch: str
    entries: list[GitStatusEntry]
    clean: bool


class GitWorkspace:
    """Wraps the git repo for a single run's `working/` directory.

    Parameters
    ----------
    working_dir : Path
        Absolute path to the run's `working/` directory. The git repo lives
        directly in this directory.
    emit : SignalEmitter | None
        Optional callback for GIT_COMMIT / GIT_MERGE / GIT_CONFLICT events.
        None disables signal emission (useful for tests).
    author_name, author_email : str
        Local-only git identity for this run. Never global.
    """

    AGENT_BRANCH_PREFIX = "agent/"
    DEFAULT_AUTHOR = ("agentflow", "bot@agentflow.local")

    def __init__(
        self,
        working_dir: Path | str,
        emit: SignalEmitter | None = None,
        author_name: str = DEFAULT_AUTHOR[0],
        author_email: str = DEFAULT_AUTHOR[1],
    ):
        self.working_dir = Path(working_dir).resolve()
        self.emit = emit
        self.author_name = author_name
        self.author_email = author_email
        self._repo: Repo | None = None

    # ── Init ────────────────────────────────────────────────────────────────

    def init(self) -> None:
        """Initialise the repo with an empty initial commit on `main`.

        Idempotent — calling `init()` on an existing repo is a no-op.
        """
        if self._repo is not None:
            return
        self.working_dir.mkdir(parents=True, exist_ok=True)
        if (self.working_dir / ".git").exists():
            self._repo = Repo(self.working_dir)
            return

        repo = Repo.init(self.working_dir, initial_branch="main")
        with repo.config_writer() as cfg:
            cfg.set_value("user", "name", self.author_name)
            cfg.set_value("user", "email", self.author_email)
            cfg.set_value("commit", "gpgsign", "false")
        # Empty initial commit so branches have somewhere to start.
        repo.index.commit(
            "chore: initialise run workspace",
            author=git.Actor(self.author_name, self.author_email),
            committer=git.Actor(self.author_name, self.author_email),
        )
        self._repo = repo

    @property
    def repo(self) -> Repo:
        if self._repo is None:
            raise GitWorkspaceError("git workspace not initialised — call init() first")
        return self._repo

    # ── Branches ────────────────────────────────────────────────────────────

    def agent_branch(self, role: str) -> str:
        """Return the branch name for an agent role, creating it from main
        if it doesn't exist yet. Idempotent."""
        branch_name = f"{self.AGENT_BRANCH_PREFIX}{role}"
        if branch_name not in [h.name for h in self.repo.heads]:
            main = self.repo.heads["main"]
            self.repo.create_head(branch_name, main.commit)
        return branch_name

    # ── Commit ──────────────────────────────────────────────────────────────

    def commit(
        self,
        role: str,
        message: str,
        files: list[str] | None = None,
    ) -> GitCommitInfo:
        """Stage and commit on the agent's branch.

        Parameters
        ----------
        role : str
            Agent role; the commit lands on `agent/{role}`.
        message : str
            Commit message. Convention is conventional-commits (`feat:`, `fix:`, etc.)
            but not enforced — the agent owns its own messages.
        files : list[str] | None
            Specific paths (relative to `working/`) to stage. If None, all
            tracked + untracked changes are staged.

        Returns the new commit's metadata.
        Posts GIT_COMMIT via the emitter callback.
        """
        if not message or not message.strip():
            raise ValueError("commit message must not be empty")

        branch = self.agent_branch(role)
        self.repo.git.checkout(branch)

        if files is None:
            self.repo.git.add(A=True)
        else:
            for f in files:
                self.repo.git.add(f)

        if not self.repo.is_dirty(index=True, working_tree=False, untracked_files=False):
            raise GitWorkspaceError(
                f"nothing staged for commit on {branch} — check that files were written"
            )

        actor = git.Actor(self.author_name, self.author_email)
        new_commit = self.repo.index.commit(message, author=actor, committer=actor)
        info = GitCommitInfo(
            hash=new_commit.hexsha,
            branch=branch,
            message=message,
            timestamp=datetime.fromtimestamp(new_commit.committed_date, tz=timezone.utc),
        )
        if self.emit is not None:
            self.emit(
                SignalType.GIT_COMMIT,
                f"{role}: committed {info.hash[:7]} — {message}",
                None,
            )
        return info

    # ── Read / diff / log ───────────────────────────────────────────────────

    def read_at(self, branch: str, path: str) -> str:
        """Read a file's contents at the tip of a branch without checking out."""
        try:
            return self.repo.git.show(f"{branch}:{path}")
        except GitCommandError as e:
            raise FileNotFoundError(f"{path!r} not found on branch {branch!r}") from e

    def diff(self, branch_a: str, branch_b: str = "main") -> str:
        """Return the unified diff of `branch_a` vs `branch_b`."""
        return self.repo.git.diff(f"{branch_b}...{branch_a}")

    def status(self) -> GitStatusResult:
        """Return uncommitted state on the current branch.

        Reports staged, unstaged, and untracked changes — the same set
        `git status --porcelain` would. Clean working tree → `clean=True`.
        """
        repo = self.repo
        entries: list[GitStatusEntry] = []

        # Staged: index vs HEAD.
        try:
            staged_diffs = repo.index.diff("HEAD")
        except Exception:
            staged_diffs = []
        for d in staged_diffs:
            state = _diff_state(d)
            path = d.b_path or d.a_path or ""
            if path:
                entries.append(GitStatusEntry(path=path, state=state, staged=True))

        # Unstaged: working tree vs index.
        try:
            unstaged_diffs = repo.index.diff(None)
        except Exception:
            unstaged_diffs = []
        for d in unstaged_diffs:
            state = _diff_state(d)
            path = d.b_path or d.a_path or ""
            if path:
                entries.append(GitStatusEntry(path=path, state=state, staged=False))

        # Untracked.
        for path in repo.untracked_files:
            entries.append(GitStatusEntry(path=path, state="untracked", staged=False))

        return GitStatusResult(
            branch=repo.head.reference.name if not repo.head.is_detached else "(detached)",
            entries=entries,
            clean=not entries,
        )

    def log(self, branch: str | None = None, limit: int = 10) -> list[GitLogEntry]:
        """Return recent commits on a branch (default: current HEAD)."""
        ref = branch if branch is not None else self.repo.head.reference.name
        entries: list[GitLogEntry] = []
        for c in self.repo.iter_commits(ref, max_count=limit):
            entries.append(GitLogEntry(
                hash=c.hexsha,
                branch=ref,
                author=str(c.author),
                message=c.message.strip() if isinstance(c.message, str) else c.message.decode().strip(),
                timestamp=datetime.fromtimestamp(c.committed_date, tz=timezone.utc),
            ))
        return entries

    # ── Merge ───────────────────────────────────────────────────────────────

    def merge(self, branch: str, into: str = "main") -> bool:
        """Merge `branch` into `into`. Returns True on clean merge, False
        on conflict (in which case the merge is aborted to leave the repo
        in a clean state and GIT_CONFLICT is emitted).

        On clean merge, GIT_MERGE is emitted.
        """
        self.repo.git.checkout(into)
        try:
            self.repo.git.merge(branch, "--no-ff", "-m", f"merge: {branch} into {into}")
        except GitCommandError as e:
            # Abort to leave the repo in a clean state.
            try:
                self.repo.git.merge("--abort")
            except GitCommandError:
                pass
            if self.emit is not None:
                self.emit(
                    SignalType.GIT_CONFLICT,
                    f"merge conflict: {branch} → {into}",
                    str(e),
                )
            return False

        if self.emit is not None:
            self.emit(
                SignalType.GIT_MERGE,
                f"merged {branch} into {into}",
                None,
            )
        return True

    # ── Phase G.5 — review-point tagging ──────────────────────────────────

    def tag(self, name: str, message: str, ref: str = "HEAD") -> bool:
        """Create an annotated git tag at `ref`. Returns False if the
        tag already exists or a git error fires; True on success.

        Used by the runner to mark APPROVED merges so users can rewind
        to a known-good review point. Tag names are
        `review/<iteration>/<reviewer-role>` so they're unique per run
        and self-describing.
        """
        try:
            self.repo.create_tag(name, ref=ref, message=message)
            return True
        except GitCommandError:
            return False
        except Exception:
            return False

    def list_tags(self) -> list[dict[str, Any]]:
        """List every tag with its commit SHA + message + tagged date.
        Used by `GET /api/runs/{id}/tags` to surface review points
        for the studio's tag panel."""
        out: list[dict[str, Any]] = []
        for tag in self.repo.tags:
            commit = tag.commit
            tag_obj = tag.tag  # None for lightweight tags; populated for annotated
            out.append({
                "name": tag.name,
                "sha": commit.hexsha,
                "short_sha": commit.hexsha[:7],
                "message": (tag_obj.message if tag_obj is not None else ""),
                "commit_message": commit.message.strip(),
                "tagged_at": (
                    tag_obj.tagged_date if tag_obj is not None else commit.committed_date
                ),
            })
        out.sort(key=lambda t: t["tagged_at"])
        return out
