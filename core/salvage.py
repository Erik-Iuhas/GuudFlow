"""Interrupted sessions: find them and send what they finished to review (FIELD_NOTES F3).

A session whose process died (host restart, worker crash that skipped its own cleanup) leaves
its backlog items `in_run` with a run id that no live run has, and no `meta.json`. Salvage
does what the end of a session would have done, from the files on disk: work out each card's
outcome from `tasks.jsonl` and `board.jsonl`, submit the run repo's `main` (everything that was
merged) to the project for a human decision, and record the item outcomes. Cards that were not
finished go back to the backlog as usual. Nothing runs again; no model is called.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.backlog import Backlog, card_outcomes
from core.signals import Signal, SignalType

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterruptedRun:
    """A session that stopped without finishing."""

    run_id: str
    items: list[str]          # backlog ids it holds
    cards_done: int
    cards_total: int
    last_activity: str        # ISO timestamp of its last board signal ("" if none)


def _jsonl(path: Path) -> list[dict[str, Any]]:  # Any: JSON rows
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _cards(run_root: Path) -> list[SimpleNamespace]:
    latest: dict[str, dict[str, Any]] = {}  # Any: JSON rows
    for row in _jsonl(run_root / "tasks.jsonl"):
        if row.get("task_id"):
            latest[str(row["task_id"])] = row
    return [SimpleNamespace(**{**r, "title": r.get("title") or r.get("goal") or ""})
            for r in latest.values()]


def _signals(run_root: Path) -> list[Signal]:
    out = []
    for row in _jsonl(run_root / "board.jsonl"):
        try:
            kind = SignalType(row.get("type"))
        except ValueError:
            continue
        out.append(Signal(type=kind, summary=str(row.get("summary") or ""),
                          from_agent=row.get("from_agent"), detail=row.get("detail")))
    return out


def _meta(run_root: Path) -> dict[str, Any] | None:  # Any: JSON
    try:
        meta = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else {}


# The worker writes meta.json with "running" when a session starts and the API reports an
# orphaned one as "stale": either way the session never finished.
_UNFINISHED = ("running", "stale")


def interrupted_runs(data_root: Path, project_id: str, live: set[str]) -> list[InterruptedRun]:
    """Sessions of the project that are neither live nor finished: those holding backlog
    items, and those (goal-only sessions hold none) whose meta.json says they never ended."""
    held: dict[str, list[str]] = {}
    for item in Backlog(data_root, project_id).items():
        if item.status == "in_run" and item.run_id and item.run_id not in live:
            held.setdefault(item.run_id, []).append(item.id)
    runs_dir = Path(data_root) / "runs"
    for meta_path in runs_dir.glob("*/meta.json") if runs_dir.is_dir() else ():
        meta = _meta(meta_path.parent) or {}
        run_id = meta_path.parent.name
        if (meta.get("project_id") == project_id and meta.get("status") in _UNFINISHED
                and run_id not in live):
            held.setdefault(run_id, [])
    out = []
    for run_id, items in sorted(held.items()):
        run_root = Path(data_root) / "runs" / run_id
        meta = _meta(run_root)
        if not run_root.is_dir() or (meta is not None and meta.get("status") not in _UNFINISHED):
            continue          # never started here, or finished (and its items are pending)
        cards = [c for c in _cards(run_root) if getattr(c, "story_id", "") in ("plan", "board")]
        board = _jsonl(run_root / "board.jsonl")
        out.append(InterruptedRun(
            run_id=run_id, items=items,
            cards_done=sum(getattr(c, "column", "") == "done" for c in cards),
            cards_total=len(cards),
            last_activity=str(board[-1].get("timestamp", "")) if board else ""))
    return out


def salvage_run(data_root: Path, project_id: str, run_id: str) -> str | None:
    """Submit an interrupted session's merged work for review. Returns the run's new state
    ("pending", "no_changes", ...) or None when nothing could be submitted."""
    from core.harness.retrospective import finalize_run
    from core.project_runs import card_results, close_run

    run_root = Path(data_root) / "runs" / run_id
    working = run_root / "workspace" / "working"
    if not working.is_dir():
        raise FileNotFoundError(f"run {run_id} has no working copy to salvage")
    summaries, costs = card_results(_signals(run_root))
    cards = card_outcomes(_cards(run_root), summaries, costs)
    try:
        finalize_run(run_root, run_id, project_id, Path(data_root))
    except Exception:  # noqa: BLE001 — the retrospective is a bonus; salvage goes on
        log.exception("retrospective for salvaged run %s failed", run_id)
    # Closed like a finished session: submitted for review, outcomes recorded per card.
    branch = close_run(Path(data_root), project_id, run_id, run_root, working, cards)
    (run_root / "meta.json").write_text(json.dumps(
        {"run_id": run_id, "project_id": project_id, "status": "salvaged",
         "working_dir": str(working)}, indent=2), encoding="utf-8")
    return branch.state if branch is not None else None
