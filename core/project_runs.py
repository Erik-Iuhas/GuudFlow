"""A run as one session of ongoing project work (plan §13).

The glue between a run and its project's long-lived state:

  prepare_run   (worker, before the run)   working/ becomes a clone of the project
                repo's main (or of a pending run it continues); the backlog items
                the run takes are snapshotted into the run folder.
  close_run     (runner, after the run)    the run's main is submitted as
                `runs/{run_id}` for a human decision; backlog outcomes recorded.
  decide_run    (API, the human)           accept: merge into main, finished items
                done, the run's carry-forward becomes the project's "last accepted";
                reject: main untouched, items reopen.

Projects whose source is `local_path_in_place` keep working in the user's
directory and skip the project repo; the backlog still works for them.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Any

import httpx

from core.signals import Signal, SignalType

from core.backlog import Backlog, BacklogItem, CardOutcome
from core.json_store import read_json, write_json
from core.project_remote import RemoteConfig, publish, sync
from core.project_repo import ProjectRepo, RunBranch

log = logging.getLogger(__name__)

SELECTION_JSON = "backlog_selection.json"
PROJECT_RUN_JSON = "project_run.json"
LAST_ACCEPTED_JSON = "last_accepted.json"
NOTEBOOKS_DIR = "notebooks"  # run_root/notebooks/{role}.md → projects/{id}/context/notebooks/
DEFAULT_TAKE = 3


@dataclass
class RunDecision:
    """What a human decision did."""

    run: RunBranch
    items: list[BacklogItem]


def prepare_run(data_root: Path, project_id: str, run_id: str, run_root: Path,
                working_dir: Path, payload: dict[str, Any], *,  # Any: job payload JSON
                in_place: bool, remote: RemoteConfig | None = None) -> list[BacklogItem]:
    """Set the run up on the project's current state; returns the backlog items taken.

    `payload` keys: `backlog_items` (list of ids, or "top"), `backlog_limit`
    (with "top", default 3), `continue_run` (a pending run to build on).

    Continuing a run (plan §14.4) merges the project's current main into it; the
    continued run's items move to this run, and conflicts become a "resolve the
    merge" item the plan must cover.
    """
    backlog = Backlog(data_root, project_id)
    items: list[BacklogItem] = []
    parent = str(payload["continue_run"]) if payload.get("continue_run") else None
    if not in_place:
        repo = ProjectRepo(data_root, project_id)
        # With a remote, sync first: it seeds a new project repo from the remote itself.
        synced = asdict(sync(repo, remote)) if remote is not None else None
        repo.ensure(seed=working_dir if (working_dir / ".git").exists() else None)
        base = repo.sha("main")
        conflicts: list[str] = []
        if parent is None:
            repo.clone_into(working_dir)
        else:
            _, conflicts = repo.continue_into(working_dir, parent, run_id)
            items += backlog.transfer(parent, run_id)
        write_json(run_root / PROJECT_RUN_JSON,
                   {"base": base, "parent_run": parent, "conflicts": conflicts,
                    "sync": synced})
        if conflicts:
            fix = backlog.add(
                f"Resolve the merge with main (continuing {parent})",
                [f"No conflict markers remain in {f}" for f in conflicts]
                + ["The changes from both sides are kept and the tests pass"],
                kind="code", size="S", priority=10_000, source=f"reconcile:{parent}",
                notes="Main changed while the earlier run waited for review. The listed files "
                      "were committed with git conflict markers.")
            items += backlog.take_for_run(run_id, [fix.id])
    wanted = payload.get("backlog_items")
    if wanted == "top":
        limit = payload.get("backlog_limit", DEFAULT_TAKE)
        items += backlog.take_for_run(run_id, None,
                                      limit if isinstance(limit, int) else DEFAULT_TAKE)
    elif wanted:
        items += backlog.take_for_run(run_id, [str(i) for i in wanted])
    if items:
        write_json(run_root / SELECTION_JSON, [asdict(i) for i in items])
    return items


def selection(run_root: Path) -> list[BacklogItem]:
    """The backlog items this run took (empty if none)."""
    rows = read_json(Path(run_root) / SELECTION_JSON, [])
    return [BacklogItem(**r) for r in rows if isinstance(r, dict)]


def close_run(data_root: Path, project_id: str, run_id: str, run_root: Path,
              working_dir: Path, cards: list[CardOutcome], *, stopped: bool = False
              ) -> RunBranch | None:
    """Submit the run to the project repo and record backlog outcomes. Never raises."""
    submitted: RunBranch | None = None
    try:
        info = read_json(Path(run_root) / PROJECT_RUN_JSON, None)
        if isinstance(info, dict) and not stopped:
            submitted = ProjectRepo(data_root, project_id).submit(
                run_id, working_dir, str(info["base"]), info.get("parent_run"))
        backlog = Backlog(data_root, project_id)
        if stopped:
            backlog.release(run_id, "run stopped")
        else:
            backlog.record_outcomes(run_id, cards)
            if submitted is not None and submitted.state == "no_changes":
                backlog.release(run_id, "run made no changes")
    except Exception:  # noqa: BLE001 — closing must never fail a finished run
        log.exception("closing run %s for project %s failed", run_id, project_id)
    return submitted


def release_crashed_run(data_root: Path, project_id: str, run_id: str, run_root: Path,
                        error: str) -> list[BacklogItem]:
    """The run crashed: the items it took go back to open (with the error). Never raises."""
    if not selection(run_root):
        return []
    try:
        return Backlog(data_root, project_id).release(run_id, f"run errored: {error}"[:300])
    except Exception:  # noqa: BLE001 — the crash is already being reported
        log.exception("releasing the items of crashed run %s failed", run_id)
        return []


def card_results(board: Iterable[Signal]) -> tuple[dict[str, str], dict[str, float]]:
    """Per card: the latest result summary, and the gateway cost of its turns.

    An AGENT_RESULT's `cost` is the role's running total at the gateway, so a turn
    costs the difference from that role's previous result.
    """
    summaries: dict[str, str] = {}
    costs: dict[str, float] = {}
    role_spend: dict[str, float] = {}
    for sig in board:
        if sig.type is not SignalType.AGENT_RESULT:
            continue
        try:
            detail = json.loads(sig.detail or "{}")
        except ValueError:
            continue
        res = detail.get("result") if isinstance(detail, dict) else None
        if not isinstance(res, dict):
            continue
        role = str(sig.from_agent or "")
        spent = float(detail.get("cost") or 0.0)
        turn_cost = max(0.0, spent - role_spend.get(role, 0.0))
        role_spend[role] = max(spent, role_spend.get(role, 0.0))
        task_id = str(res.get("task_id") or "")
        if task_id:
            costs[task_id] = round(costs.get(task_id, 0.0) + turn_cost, 6)
            if res.get("summary"):
                summaries[task_id] = str(res["summary"])[:300]
    return summaries, costs


def decide_run(data_root: Path, project_id: str, run_id: str, accept: bool,
               note: str = "", remote: RemoteConfig | None = None,
               http: httpx.Client | None = None) -> RunDecision:
    """A human accepts or rejects a pending run (raises ProjectRepoError / Conflict).

    With a remote (plan §14.1), accepting first syncs the remote into main, then
    merges the run, then publishes as configured; both results are kept on the run.
    """
    repo = ProjectRepo(data_root, project_id)
    synced = sync(repo, remote) if accept and remote is not None else None
    run = repo.accept(run_id, note) if accept else repo.reject(run_id, note)
    items = Backlog(data_root, project_id).decide(run_id, accepted=accept, note=note)
    _remember_decision(data_root, project_id, run_id, accept, note)
    if accept:
        from core.spec_adherence import record_acceptance
        record_acceptance(data_root, project_id, run_id)
        # The record goes onto main before publishing, so the published branch carries it.
        write_record_files(data_root, project_id, run_id)
        if remote is not None:
            published = publish(repo, remote, repo.get(run_id) or run, http=http)
            repo.record(run_id, sync=asdict(synced) if synced else None,
                        published=asdict(published))
        run = repo.get(run_id) or run
        keep_notebooks(data_root, project_id, run_id)
        carry = Path(data_root) / "runs" / run_id / "carry_forward.json"
        target = Path(data_root) / "projects" / project_id / "context" / LAST_ACCEPTED_JSON
        if carry.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(carry, target)
    return RunDecision(run=run, items=items)


def _remember_decision(data_root: Path, project_id: str, run_id: str, accept: bool,
                       note: str) -> None:
    """Jack learns from the decision (plan §10.3). Never raises."""
    try:
        from core.pm_memory import record_session_outcome
        record_session_outcome(data_root, project_id, run_id, accept, note)
    except Exception:  # noqa: BLE001 — a memory failure must not undo a human's decision
        log.exception("the project manager's memory of run %s failed", run_id)


def _notebook_dir(data_root: Path, project_id: str) -> Path:
    return Path(data_root) / "projects" / project_id / "context" / NOTEBOOKS_DIR


def keep_notebooks(data_root: Path, project_id: str, run_id: str) -> list[str]:
    """An accepted run's notebooks become the project's (one per role). Never raises.

    Only accepted runs: a rejected session's notes describe code that never reached main.
    Returns the roles whose notebook was kept.
    """
    kept: list[str] = []
    try:
        source = Path(data_root) / "runs" / run_id / NOTEBOOKS_DIR
        if not source.is_dir():
            return kept
        target = _notebook_dir(data_root, project_id)
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.glob("*.md")):
            if path.is_file() and not path.is_symlink():
                shutil.copyfile(path, target / path.name)
                kept.append(path.stem)
    except OSError:
        log.exception("keeping the notebooks of run %s failed", run_id)
    return kept


def load_notebooks(data_root: Path | None, project_id: str | None,
                   roles: Iterable[str]) -> dict[str, str]:
    """Each role's notebook from the project's last accepted session ({} if none)."""
    if data_root is None or not project_id:
        return {}
    folder = _notebook_dir(data_root, project_id)
    notes: dict[str, str] = {}
    for role in roles:
        path = folder / f"{role}.md"
        try:
            text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except OSError:
            text = ""
        if text:
            notes[role] = text
    return notes


def keeps_record_in_repo(data_root: Path, project_id: str) -> bool:
    """Has the project opted in to `SPEC.md` and `docs/decisions/` in its repo?"""
    projects = read_json(Path(data_root) / "projects.json", {})
    doc = projects.get(project_id) if isinstance(projects, dict) else None
    record = doc.get("record") if isinstance(doc, dict) else None
    return bool(isinstance(record, dict) and record.get("keep_in_repo"))


def write_record_files(data_root: Path, project_id: str, run_id: str) -> str | None:
    """Commit the spec and decision records onto main after an accepted run, when the
    project opted in. Never raises: the merge has already happened, and the record is
    regenerated on the next accept. Returns the new main sha or None."""
    if not keeps_record_in_repo(data_root, project_id):
        return None
    try:
        from core.record import spec_files
        files = spec_files(data_root, project_id)
        if not files:
            return None
        return ProjectRepo(data_root, project_id).commit_files(
            files, f"docs: update the project record after run {run_id}")
    except Exception:  # noqa: BLE001 — see docstring
        log.exception("writing the project record for run %s failed", run_id)
        return None


def last_accepted_text(data_root: Path, project_id: str) -> str:
    """The carry-forward text of the project's last accepted run ("" if none)."""
    doc = read_json(Path(data_root) / "projects" / project_id / "context" / LAST_ACCEPTED_JSON,
                    {})
    text = doc.get("text") if isinstance(doc, dict) else None
    return str(text).strip() if text else ""


def pending_summary(data_root: Path, project_id: str) -> list[dict[str, Any]]:  # Any: JSON rows
    """Runs waiting for a decision, newest first (for Studio and the meta-agent)."""
    return [json.loads(json.dumps(asdict(r))) for r in ProjectRepo(data_root, project_id).runs()
            if r.state == "pending"]
