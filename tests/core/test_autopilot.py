"""Tests for core.autopilot (plan §14.7): scheduled runs within budget, guarded auto-accept."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.autopilot import (
    AutopilotConfig, AutopilotError, should_auto_accept, tick, week_spend,
)
from core.backlog import Backlog, CardOutcome
from worker.queue import InMemoryJobQueue, JOB_RUNNING

H = 3600.0
NOW = 1_800_000_000.0


class Store:
    def __init__(self, *projects: dict[str, Any]) -> None:
        self.projects = list(projects)

    async def list(self) -> list[dict[str, Any]]:
        return self.projects


def _project(pid: str = "p", **pilot: Any) -> dict[str, Any]:
    cfg = {"enabled": True, "every_hours": 6, "take": 2, "max_usd_per_run": 3,
           "max_usd_per_week": 10, **pilot}
    return {"project_id": pid, "approval_gates": {"autopilot": cfg, "token_budget_usd": 2}}


def _cost(data: Path, run_id: str, usd: float) -> None:
    run = data / "runs" / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "llm_costs.jsonl").write_text(json.dumps({"role": "dev", "cost_usd": usd}) + "\n")


@pytest.mark.parametrize("raw", [{"enabled": True, "every_hours": 0.1}, {"take": 0},
                                 {"max_usd_per_week": -1}, {"every_hours": "soon"}, "on"])
def test_bad_settings(raw: Any) -> None:
    with pytest.raises(AutopilotError):
        AutopilotConfig.from_settings(raw)


async def test_starts_due_runs_with_the_tighter_budget(tmp_path: Path) -> None:
    Backlog(tmp_path, "p").add("Item", ["works"])
    queue = InMemoryJobQueue()
    started = await tick(Store(_project()), queue, tmp_path, now=NOW)
    assert len(started) == 1
    job = (await queue.list(project_id="p"))[0]
    assert job.payload == {"confirm": True, "backlog_items": "top", "backlog_limit": 2,
                           "budget_usd": 2, "autopilot": {"auto_accept": False}}
    # Not again while it is queued, nor before every_hours has passed.
    assert await tick(Store(_project()), queue, tmp_path, now=NOW + 7 * H) == []
    await queue.update_status(job.job_id, "complete")
    assert await tick(Store(_project()), queue, tmp_path, now=NOW + 5 * H) == []
    assert len(await tick(Store(_project()), queue, tmp_path, now=NOW + 7 * H)) == 1


async def test_skips_disabled_empty_blocked_and_over_budget(tmp_path: Path) -> None:
    queue = InMemoryJobQueue()
    assert await tick(Store(_project(enabled=False)), queue, tmp_path, now=NOW) == []
    assert await tick(Store(_project()), queue, tmp_path, now=NOW) == []        # no items
    b = Backlog(tmp_path, "p")
    first = b.add("Schema", [])
    b.add("API", [], depends_on=[first.id])
    b.drop(first.id)
    assert await tick(Store(_project()), queue, tmp_path, now=NOW) == []        # API blocked
    b.reopen(first.id)
    started = await tick(Store(_project()), queue, tmp_path, now=NOW)
    for job in await queue.list(project_id="p"):
        await queue.update_status(job.job_id, "complete")
    _cost(tmp_path, started[0], 12.0)
    assert week_spend(tmp_path, "p", NOW + 7 * H) == 12.0
    assert await tick(Store(_project()), queue, tmp_path, now=NOW + 7 * H) == []  # over 10/week
    assert week_spend(tmp_path, "p", NOW + 8 * 24 * H) == 0.0                  # a week later


async def test_a_running_job_blocks_autopilot(tmp_path: Path) -> None:
    from worker.queue import Job
    Backlog(tmp_path, "p").add("Item", [])
    queue = InMemoryJobQueue()
    await queue.enqueue(Job(job_id="j", project_id="p", run_id="run_x"))
    await queue.update_status("j", JOB_RUNNING)
    assert await tick(Store(_project()), queue, tmp_path, now=NOW) == []


def test_auto_accept_is_narrow() -> None:
    on = {"auto_accept": True}
    ok = [CardOutcome("a", "done", "B1")]
    assert should_auto_accept(on, ["done"], ok, "passed") == (
        True, "every item finished and the check passed")
    assert not should_auto_accept({"auto_accept": False}, ["done"], ok, "passed")[0]
    assert should_auto_accept(None, ["done"], ok, "passed")[1] == "auto-accept is off"
    assert not should_auto_accept(on, [], ok, "passed")[0]
    assert not should_auto_accept(on, ["done", "partial"], ok, "passed")[0]
    assert not should_auto_accept(on, ["done"], [CardOutcome("b", "needs_attention")], "passed")[0]
    assert should_auto_accept(on, ["done"], ok, None)[1] == "no check_command is set"
    assert should_auto_accept(on, ["done"], ok, "failed")[1] == "the check failed"
