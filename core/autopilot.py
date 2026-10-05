"""Autopilot: runs that start themselves, within budget (plan §14.7).

Settings (`approval_gates.autopilot`):

    {"enabled": true, "every_hours": 6, "take": 2,
     "max_usd_per_run": 3.0, "max_usd_per_week": 20.0, "auto_accept": false}

`tick()` (called every minute by the API's background loop) starts a run for a
project when all of these hold: autopilot is enabled; no run of the project is
queued or running; the last autopilot start was at least `every_hours` ago; there
are open backlog items whose dependencies are done; and the gateway spend of the
autopilot's runs over the last seven days is under `max_usd_per_week`. The run
takes the top `take` items with a per-run spend cap of `max_usd_per_run` (never
above the project's own `token_budget_usd`).

Auto-accept (`should_auto_accept`) is deliberately narrow: only an autopilot run,
only when every item it took finished, no card needs attention, the project's
`check_command` ran and passed (no check, no auto-accept), and the merge is clean.
Anything else waits for a human, as usual.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.backlog import Backlog, CardOutcome
from core.json_store import locked_json, read_json

log = logging.getLogger(__name__)

STATE_JSON = "autopilot.json"
RUN_MARKER = "autopilot_run.json"
WEEK = 7 * 24 * 3600.0
TICK_SECONDS = 60.0


class AutopilotError(ValueError):
    """Invalid autopilot settings."""


@dataclass(frozen=True)
class AutopilotConfig:
    """Validated `approval_gates.autopilot`."""

    enabled: bool = False
    every_hours: float = 24.0
    take: int = 2
    max_usd_per_run: float = 5.0
    max_usd_per_week: float = 20.0
    auto_accept: bool = False

    @classmethod
    def from_settings(cls, raw: Any) -> "AutopilotConfig":  # Any: settings JSON
        """Defaults (disabled) when unset; AutopilotError when invalid."""
        if not raw:
            return cls()
        if not isinstance(raw, dict):
            raise AutopilotError("autopilot must be an object")
        try:
            cfg = cls(enabled=bool(raw.get("enabled", False)),
                      every_hours=float(raw.get("every_hours", 24.0)),
                      take=int(raw.get("take", 2)),
                      max_usd_per_run=float(raw.get("max_usd_per_run", 5.0)),
                      max_usd_per_week=float(raw.get("max_usd_per_week", 20.0)),
                      auto_accept=bool(raw.get("auto_accept", False)))
        except (TypeError, ValueError) as exc:
            raise AutopilotError(f"autopilot: {exc}") from None
        if cfg.every_hours < 0.25 or not 1 <= cfg.take <= 10:
            raise AutopilotError("autopilot: every_hours >= 0.25 and take between 1 and 10")
        if cfg.max_usd_per_run <= 0 or cfg.max_usd_per_week <= 0:
            raise AutopilotError("autopilot: budgets must be positive")
        return cfg


def _state_path(data_root: Path, project_id: str) -> Path:
    return Path(data_root) / "projects" / project_id / STATE_JSON


def run_cost(data_root: Path, run_id: str) -> float:
    """Gateway spend of one run (USD), from its cost log."""
    from core.oneshot import TOTAL_ROLE, summarise_costs
    rows = summarise_costs(Path(data_root) / "runs" / run_id)
    total = next((r for r in rows if r.get("role") == TOTAL_ROLE), None)
    return float(total.get("cost_usd") or 0.0) if total else 0.0


def week_spend(data_root: Path, project_id: str, now: float) -> float:
    """What the autopilot's runs of this project cost over the last seven days."""
    state = read_json(_state_path(data_root, project_id), {})
    return round(sum(run_cost(data_root, r["run_id"]) for r in state.get("runs", [])
                     if isinstance(r, dict) and float(r.get("at", 0)) >= now - WEEK), 4)


def status(data_root: Path, project_id: str, cfg: AutopilotConfig,
           now: float) -> dict[str, Any]:  # Any: JSON for Studio
    """When the next run is due and how much of the weekly budget is spent."""
    state = read_json(_state_path(data_root, project_id), {})
    last = float(state.get("last_start") or 0.0)
    return {"enabled": cfg.enabled, "last_start": last or None,
            "next_due": (last + cfg.every_hours * 3600) if last else now,
            "week_spend_usd": week_spend(data_root, project_id, now),
            "max_usd_per_week": cfg.max_usd_per_week}


def _ready_items(backlog: Backlog) -> int:
    items = backlog.items()
    done = {i.id for i in items if i.status == "done"}
    return sum(1 for i in items if i.status == "open" and set(i.depends_on) <= done)


async def tick(project_store: Any, queue: Any, data_root: Path,  # Any: api stores
               now: float | None = None) -> list[str]:
    """Start every due autopilot run; returns the new run ids."""
    from worker.queue import JOB_QUEUED, JOB_RUNNING, Job

    now = time.time() if now is None else now
    started: list[str] = []
    for project in await project_store.list():
        project_id = str(project.get("project_id") or "")
        gates = project.get("approval_gates") or {}
        try:
            cfg = AutopilotConfig.from_settings(gates.get("autopilot"))
        except AutopilotError as exc:
            log.warning("autopilot for %s is misconfigured: %s", project_id, exc)
            continue
        if not cfg.enabled or not project_id:
            continue
        jobs = await queue.list(project_id=project_id, limit=500)
        if any(j.status in (JOB_QUEUED, JOB_RUNNING) for j in jobs):
            continue
        info = status(data_root, project_id, cfg, now)
        if now < info["next_due"] or info["week_spend_usd"] >= cfg.max_usd_per_week:
            continue
        if _ready_items(Backlog(data_root, project_id)) == 0:
            continue
        budget = cfg.max_usd_per_run
        own = gates.get("token_budget_usd")
        if isinstance(own, (int, float)) and own > 0:
            budget = min(budget, float(own))
        run_id = f"run_{uuid4().hex[:10]}"
        await queue.enqueue(Job(job_id=f"job_{uuid4().hex[:10]}", project_id=project_id,
                                run_id=run_id, payload={
                                    "confirm": True, "backlog_items": "top",
                                    "backlog_limit": cfg.take, "budget_usd": budget,
                                    "autopilot": {"auto_accept": cfg.auto_accept}}))
        with locked_json(_state_path(data_root, project_id), {}) as state:
            state["last_start"] = now
            state.setdefault("runs", []).append({"run_id": run_id, "at": now})
            state["runs"] = state["runs"][-200:]
        started.append(run_id)
        log.info("autopilot started %s for %s", run_id, project_id)
    return started


async def run_loop(app_state: Any, interval: float = TICK_SECONDS) -> None:  # Any: app.state
    """The API's background loop: one tick per `interval` until cancelled."""
    while True:
        try:
            await tick(app_state.project_store, app_state.queue, Path(app_state.data_root))
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            log.exception("autopilot tick failed")
        await asyncio.sleep(interval)


def should_auto_accept(marker: dict[str, Any] | None, items_pending: Sequence[str | None],
                       cards: Sequence[CardOutcome], check_status: str | None
                       ) -> tuple[bool, str]:  # Any: marker JSON
    """Auto-accept only a clean, finished, checked autopilot run (see the module doc)."""
    if not marker or not marker.get("auto_accept"):
        return False, "auto-accept is off"
    if not items_pending:
        return False, "the run took no backlog items"
    if any(p != "done" for p in items_pending):
        return False, "not every item finished"
    if any(c.column == "needs_attention" for c in cards):
        return False, "a card needs attention"
    if check_status != "passed":
        return False, ("no check_command is set" if check_status is None
                       else f"the check {check_status}")
    return True, "every item finished and the check passed"
