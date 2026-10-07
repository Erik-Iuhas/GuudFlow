"""Interrupted sessions are found and their merged work goes to review (core.salvage, F3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.backlog import Backlog
from core.project_repo import ProjectRepo
from core.project_runs import prepare_run
from core.salvage import interrupted_runs, salvage_run


def _interrupted(data: Path) -> tuple[Path, list[str]]:
    backlog = Backlog(data, "p")
    a = backlog.add("Dash", ["dash works"])
    b = backlog.add("Families", ["families work"])
    run_root = data / "runs" / "run_x"
    working = run_root / "workspace" / "working"
    prepare_run(data, "p", "run_x", run_root, working, {"backlog_items": [a.id, b.id]},
                in_place=False)
    (working / "dash.js").write_text("export function dash() {}\n")
    subprocess.run(["git", "add", "-A"], cwd=working, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm",
                    "feat: dash"], cwd=working, check=True)
    rows = [{"task_id": "T1", "story_id": "plan", "title": "Dash", "column": "done",
             "backlog_id": a.id, "assigned_to": "dev"},
            {"task_id": "T2", "story_id": "plan", "title": "Families", "column": "in_progress",
             "backlog_id": b.id, "assigned_to": "dev"}]
    (run_root / "tasks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (run_root / "board.jsonl").write_text(json.dumps(
        {"type": "AGENT_RESULT", "from_agent": "dev", "summary": "r",
         "detail": json.dumps({"result": {"task_id": "T1", "summary": "dash done"}, "cost": 1.5}),
         "timestamp": "2026-10-07T10:00:00+00:00"}) + "\n")
    return run_root, [a.id, b.id]


def test_finds_interrupted_sessions_but_not_live_or_finished_ones(tmp_path: Path) -> None:
    run_root, items = _interrupted(tmp_path)
    found = interrupted_runs(tmp_path, "p", live=set())
    assert [(r.run_id, r.items, r.cards_done, r.cards_total) for r in found] == [
        ("run_x", items, 1, 2)]
    assert found[0].last_activity.startswith("2026-10-07")
    assert interrupted_runs(tmp_path, "p", live={"run_x"}) == []
    (run_root / "meta.json").write_text("{}")
    assert interrupted_runs(tmp_path, "p", live=set()) == []


def test_salvage_submits_merged_work_and_reopens_unfinished_items(tmp_path: Path) -> None:
    _run_root, (done_id, open_id) = _interrupted(tmp_path)
    assert salvage_run(tmp_path, "p", "run_x") == "pending"
    assert ProjectRepo(tmp_path, "p").get("run_x").state == "pending"   # type: ignore[union-attr]
    backlog = Backlog(tmp_path, "p")
    assert backlog.get(done_id).pending == "done"
    assert backlog.get(open_id).pending != "done"
    assert interrupted_runs(tmp_path, "p", live=set()) == []            # handled now
