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


def _persona_slug(name: str) -> str:
    """Convert a persona display name to a git-safe slug.

    "Diana Park" → "diana_park", "Mike O'Brien" → "mike_o_brien".
    Non-alphanumeric runs collapse to a single underscore; leading/
    trailing underscores are stripped.
    """
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug or "agent"


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
    hired_personas : dict[str, str] | None
        Mapping of role → persona display name. When present, branches are
        named ``{persona_slug}/{role}`` instead of ``agent/{role}``.
    """

    AGENT_BRANCH_PREFIX = "agent/"
    DEFAULT_AUTHOR = ("agentflow", "bot@agentflow.local")

    def __init__(
        self,
        working_dir: Path | str,
        emit: SignalEmitter | None = None,
        author_name: str = DEFAULT_AUTHOR[0],
        author_email: str = DEFAULT_AUTHOR[1],
        hired_personas: dict[str, str] | None = None,
    ):
        self.working_dir = Path(working_dir).resolve()
        self.emit = emit
        self.author_name = author_name
        self.author_email = author_email
        self._hired_personas: dict[str, str] = hired_personas or {}
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

    def agent_branch(self, role: str, parent_branches: list[str] | None = None) -> str:
        """Return the branch name for an agent role, creating it if needed.

        Branch is ``{persona_slug}/{role}`` when a persona display name is
        known for the role (via ``hired_personas``), otherwise ``agent/{role}``.

        When ``parent_branches`` is provided the new branch forks from the tip
        of the first parent rather than from ``main``. This keeps the git graph
        mirroring the pipeline DAG: each agent's branch visually descends from
        the agent(s) it depends on, not from the empty initialisation commit.

        For fan-in roles (multiple parents) the caller is still responsible for
        merging the remaining parents after this returns — only the *first*
        parent determines the fork point so the branch is created cleanly.

        Idempotent: if the branch already exists the parent list is ignored.
        """
        persona = self._hired_personas.get(role)
        prefix = _persona_slug(persona) if persona else "agent"
        branch_name = f"{prefix}/{role}"
        if branch_name not in [h.name for h in self.repo.heads]:
            # Fork from first parent's tip so history follows the pipeline.
            # Falls back to main when the parent hasn't committed yet (the
            # runner's _premerge_upstream_deps will merge it in later).
            start_commit = self.repo.heads["main"].commit
            if parent_branches:
                for pb in parent_branches:
                    heads_by_name = {h.name: h for h in self.repo.heads}
                    if pb in heads_by_name:
                        start_commit = heads_by_name[pb].commit
                        break
            self.repo.create_head(branch_name, start_commit)
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

        persona_name = self._hired_personas.get(role, role)
        actor = git.Actor(f"{persona_name} ({role})", f"{role}@agentflow.local")
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

    def diff(self, branch_a: str, branch_b: str = "main", path: str | None = None) -> str:
        """Return the unified diff of `branch_a` vs `branch_b`.

        If `path` is given, restrict the diff to that single file so
        reviewers can work file-by-file without blowing the token budget.
        """
        range_arg = f"{branch_b}...{branch_a}"
        if path:
            return self.repo.git.diff(range_arg, "--", path)
        return self.repo.git.diff(range_arg)

    def changed_files(self, branch_a: str, branch_b: str = "main") -> list[str]:
        """Return the list of file paths changed in `branch_a` vs `branch_b`."""
        output: str = self.repo.git.diff("--name-only", f"{branch_b}...{branch_a}")
        return [p for p in output.splitlines() if p.strip()]

    def head_sha(self) -> str | None:
        """Current HEAD commit SHA, or None if the repo has no commits yet."""
        try:
            return self.repo.head.commit.hexsha
        except (ValueError, Exception):
            return None

    def diff_against_ref(self, ref: str, path: str | None = None) -> str:
        """Unified diff of the working tree vs `ref`. Restrict to `path` if given.

        Used for story-scoped diffs: `ref` is the story's `base_ref`, captured
        when the story opened. Includes uncommitted changes in the working
        tree so agents see in-flight edits, not just committed ones.
        """
        if path:
            return self.repo.git.diff(ref, "--", path)
        return self.repo.git.diff(ref)

    def changed_files_against_ref(self, ref: str) -> list[str]:
        """Paths changed in the working tree vs `ref`. Working-tree-inclusive."""
        try:
            output: str = self.repo.git.diff("--name-only", ref)
        except GitCommandError:
            return []
        return [p for p in output.splitlines() if p.strip()]

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

        Only emits GIT_MERGE when HEAD actually advances. No-op merges
        ("Already up to date") return True silently.
        """
        self.repo.git.checkout(into)
        pre_sha = self.repo.heads[into].commit.hexsha if into in [h.name for h in self.repo.heads] else None
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

        post_sha = self.repo.heads[into].commit.hexsha if into in [h.name for h in self.repo.heads] else None
        if self.emit is not None and post_sha != pre_sha:
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
