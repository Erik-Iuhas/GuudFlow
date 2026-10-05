"""The project's long-lived git repo: runs build on it, humans accept them into it.

A GuudFlow project is ongoing development, not a series of isolated runs
(plan §13). Each project (except `local_path_in_place` ones, whose repo is the
user's own directory) has one bare repo at `data/projects/{id}/repo.git`:

  - It is seeded once, from the project's source repo or empty.
  - Every run starts as a clone of its `main` (full history), or of a pending
    run's branch when the run continues that run before it was accepted.
  - At run end the run's `main` is stored as `runs/{run_id}` and waits.
  - A human accepts it (merged into `main` with `--no-ff`) or rejects it (the
    branch stays for reference, `main` is untouched). Nothing an agent did
    reaches the project's `main` without that decision.

Run states live in `data/projects/{id}/runs_state.json` (locked, see
`core.json_store`). Every git call runs with hooks and fsmonitor off.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from core.json_store import locked_json, read_json

RunState = Literal["pending", "accepted", "rejected", "no_changes"]
MAIN = "main"
_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_SAFE = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main")
_AUTHOR = ("-c", "user.name=GuudFlow", "-c", "user.email=runner@guudflow.local")


class ProjectRepoError(RuntimeError):
    """A project repo operation failed (bad state, unknown run, git error)."""


class ProjectRepoConflict(ProjectRepoError):
    """Accepting a run conflicts with the project's main (main moved since the run began)."""

    def __init__(self, run_id: str, files: list[str]) -> None:
        super().__init__(f"run {run_id} conflicts with main in: {', '.join(files) or '?'}")
        self.files = files


@dataclass
class RunBranch:
    """One run's contribution to the project."""

    run_id: str
    state: RunState
    base: str                      # project main (or parent run head) the run started from
    head: str = ""                 # tip of runs/{run_id}
    commits: int = 0
    files: list[str] = field(default_factory=list)
    submitted_at: float = 0.0
    decided_at: float | None = None
    merged: str = ""               # main after the merge (accepted runs)
    note: str = ""
    parent_run: str | None = None  # the pending run it continued, if any

    @property
    def branch(self) -> str:
        """The run's branch in the project repo."""
        return f"runs/{self.run_id}"


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *_SAFE, *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProjectRepoError(f"git {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _check_run_id(run_id: str) -> None:
    if not _RUN_ID.match(run_id):
        raise ProjectRepoError(f"invalid run id {run_id!r}")


class ProjectRepo:
    """The bare repo and run states of one project."""

    def __init__(self, data_root: Path, project_id: str) -> None:
        self.project_dir = Path(data_root) / "projects" / project_id
        self.path = self.project_dir / "repo.git"
        self.state_path = self.project_dir / "runs_state.json"

    # ── setup ───────────────────────────────────────────────────────────────
    def exists(self) -> bool:
        """Has the project repo been created?"""
        return (self.path / "HEAD").is_file()

    def ensure(self, seed: Path | None = None) -> str:
        """Create the repo once (from `seed`'s `main` if it is a git repo); return main's sha."""
        if not self.exists():
            self.path.mkdir(parents=True, exist_ok=True)
            _git("init", "--quiet", "--bare", "-b", MAIN, str(self.path))
            if seed is not None and (seed / ".git").exists():
                _git("fetch", "--quiet", str(seed), f"{MAIN}:{MAIN}", cwd=self.path)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    _git("init", "--quiet", "-b", MAIN, tmp)
                    _git(*_AUTHOR, "commit", "--quiet", "--allow-empty", "-m",
                         "chore: start the project repo", cwd=Path(tmp))
                    _git("push", "--quiet", str(self.path), f"{MAIN}:{MAIN}", cwd=Path(tmp))
        return self.sha(MAIN)

    def sha(self, ref: str) -> str:
        """Commit sha of `ref` in the project repo."""
        return _git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=self.path)

    def clone_into(self, working_dir: Path, run_id: str | None = None) -> str:
        """Replace `working_dir` with a clone of main (or of pending run `run_id`); return HEAD."""
        ref = MAIN
        if run_id is not None:
            run = self.get(run_id)
            if run is None or run.state != "pending":
                raise ProjectRepoError(f"run {run_id} is not pending; continue from main instead")
            ref = run.branch
        if working_dir.exists():
            shutil.rmtree(working_dir)
        working_dir.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", "--no-local", str(self.path), str(working_dir))
        _git("checkout", "--quiet", "-B", MAIN, f"origin/{ref}", cwd=working_dir)
        # The run never pushes: the runner submits it from the project side.
        _git("remote", "remove", "origin", cwd=working_dir)
        _git("config", "user.name", "GuudFlow", cwd=working_dir)
        _git("config", "user.email", "runner@guudflow.local", cwd=working_dir)
        return _git("rev-parse", "HEAD", cwd=working_dir)

    # ── runs ────────────────────────────────────────────────────────────────
    def submit(self, run_id: str, working_dir: Path, base: str,
               parent_run: str | None = None) -> RunBranch:
        """Store the run's main as `runs/{run_id}`, pending a human decision."""
        _check_run_id(run_id)
        branch = f"runs/{run_id}"
        _git("fetch", "--quiet", str(working_dir), f"+{MAIN}:refs/heads/{branch}", cwd=self.path)
        head = self.sha(branch)
        commits = int(_git("rev-list", "--count", f"{base}..{head}", cwd=self.path) or 0)
        files = [f for f in _git("diff", "--name-only", base, head, cwd=self.path).splitlines() if f]
        run = RunBranch(run_id=run_id, state="pending" if commits else "no_changes", base=base,
                        head=head, commits=commits, files=files[:500], submitted_at=time.time(),
                        parent_run=parent_run)
        with locked_json(self.state_path, {}) as states:
            states[run_id] = asdict(run)
        return run

    def runs(self) -> list[RunBranch]:
        """Every submitted run, newest first."""
        states = read_json(self.state_path, {})
        runs = [RunBranch(**row) for row in states.values() if isinstance(row, dict)]
        return sorted(runs, key=lambda r: r.submitted_at, reverse=True)

    def get(self, run_id: str) -> RunBranch | None:
        """One run's state, or None."""
        row = read_json(self.state_path, {}).get(run_id)
        return RunBranch(**row) if isinstance(row, dict) else None

    def changes(self, run_id: str, limit: int = 200_000) -> str:
        """What accepting the run would bring in: stat + patch of main...runs/{id}."""
        run = self._require(run_id)
        stat = _git("diff", "--stat", f"{MAIN}...{run.branch}", cwd=self.path)
        patch = _git("diff", f"{MAIN}...{run.branch}", cwd=self.path)
        text = f"{stat}\n\n{patch}" if stat else "(no changes)"
        return text if len(text) <= limit else text[:limit] + "\n[truncated]"

    def accept(self, run_id: str, note: str = "") -> RunBranch:
        """Merge the run into main. Raises ProjectRepoConflict if main moved incompatibly."""
        with locked_json(self.state_path, {}) as states:
            run = self._pending(states, run_id)
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / "merge"
                _git("clone", "--quiet", "--no-local", "--branch", MAIN, str(self.path), str(work))
                _git("fetch", "--quiet", "origin", f"{run.branch}:{run.branch}", cwd=work)
                try:
                    _git(*_AUTHOR, "merge", "--no-ff", "--quiet", "-m",
                         f"merge: run {run_id}" + (f"\n\n{note}" if note else ""), run.branch,
                         cwd=work)
                except ProjectRepoError:
                    files = _git("diff", "--name-only", "--diff-filter=U", cwd=work).splitlines()
                    _git("merge", "--abort", cwd=work)
                    raise ProjectRepoConflict(run_id, [f for f in files if f]) from None
                _git("push", "--quiet", "origin", MAIN, cwd=work)
            run.state, run.decided_at, run.note = "accepted", time.time(), note
            run.merged = self.sha(MAIN)
            states[run_id] = asdict(run)
        return run

    def reject(self, run_id: str, note: str = "") -> RunBranch:
        """Leave main as it is; the branch stays for reference."""
        with locked_json(self.state_path, {}) as states:
            run = self._pending(states, run_id)
            run.state, run.decided_at, run.note = "rejected", time.time(), note
            states[run_id] = asdict(run)
        return run

    def _require(self, run_id: str) -> RunBranch:
        run = self.get(run_id)
        if run is None:
            raise ProjectRepoError(f"run {run_id} was never submitted to this project")
        return run

    @staticmethod
    def _pending(states: dict[str, dict[str, object]], run_id: str) -> RunBranch:
        row = states.get(run_id)
        if not isinstance(row, dict):
            raise ProjectRepoError(f"run {run_id} was never submitted to this project")
        run = RunBranch(**row)  # type: ignore[arg-type]
        if run.state != "pending":
            raise ProjectRepoError(f"run {run_id} is {run.state}, not pending")
        return run
