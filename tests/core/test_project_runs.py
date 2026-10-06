"""Tests for core.project_runs helpers (plan §13, §14)."""

from __future__ import annotations

import json

from core.project_runs import card_results
from core.signals import Signal, SignalType


def _result(role: str, task: str, cost: float, summary: str = "") -> Signal:
    return Signal(type=SignalType.AGENT_RESULT, from_agent=role, summary="r",
                  detail=json.dumps({"result": {"task_id": task, "summary": summary},
                                     "cost": cost}))


def test_turn_costs_are_differences_of_the_roles_running_total() -> None:
    board = [_result("dev", "T1", 0.10, "first try"), _result("rev", "T1", 0.05),
             _result("dev", "T1", 0.25, "fixed it"), _result("dev", "T2", 0.30),
             Signal(type=SignalType.STARTED, from_agent="dev", summary="x")]
    summaries, costs = card_results(board)
    assert summaries == {"T1": "fixed it"}
    assert costs == {"T1": 0.30, "T2": 0.05}


import subprocess
from pathlib import Path

import pytest

from core.backlog import Backlog, CardOutcome
from core.project_repo import ProjectRepo, ProjectRepoError
from core.project_runs import prepare_run, selection


def _commit(work: Path, name: str, text: str) -> None:
    (work / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm",
                    f"feat: {name}"], cwd=work, check=True)


def _submitted_pair(data: Path) -> ProjectRepo:
    """run_a and run_b both change same.py; run_a is accepted first."""
    repo = ProjectRepo(data, "p")
    base = repo.ensure(None)
    for run_id, text in (("run_a", "a\n"), ("run_b", "b\n")):
        work = data / "tmp" / run_id
        repo.clone_into(work)
        _commit(work, "same.py", text)
        repo.submit(run_id, work, base)
    repo.accept("run_a")
    return repo


def test_continuing_a_conflicting_run_reconciles_it(tmp_path: Path) -> None:
    data = tmp_path / "data"
    repo = _submitted_pair(data)
    backlog = Backlog(data, "p")
    item = backlog.add("Feature B", ["works"])
    backlog.take_for_run("run_b", ids=[item.id])
    backlog.record_outcomes("run_b", [CardOutcome("B", "done", item.id)])

    run_root = data / "runs" / "run_c"
    working = run_root / "workspace" / "working"
    taken = prepare_run(data, "p", "run_c", run_root, working, {"continue_run": "run_b"},
                        in_place=False)
    assert repo.get("run_b").state == "superseded"          # type: ignore[union-attr]
    assert "<<<<<<< " in (working / "same.py").read_text()  # committed with markers
    ids = {i.id for i in taken}
    assert item.id in ids and backlog.get(item.id).run_id == "run_c"   # moved over
    fix = next(i for i in taken if i.source == "reconcile:run_b")
    assert fix.acceptance[0] == "No conflict markers remain in same.py"
    assert {i.id for i in selection(run_root)} == ids

    # Submitting with markers left: accept is refused.
    info = __import__("json").loads((run_root / "project_run.json").read_text())
    repo.submit("run_c", working, info["base"], "run_b")
    with pytest.raises(ProjectRepoError, match="conflict markers"):
        repo.accept("run_c")
    # Resolved in a later commit (as the run's agents would): accepted cleanly.
    run_root2 = data / "runs" / "run_d"
    work2 = run_root2 / "workspace" / "working"
    prepare_run(data, "p", "run_d", run_root2, work2, {"continue_run": "run_c"}, in_place=False)
    _commit(work2, "same.py", "a\nb\n")
    info2 = __import__("json").loads((run_root2 / "project_run.json").read_text())
    repo.submit("run_d", work2, info2["base"], "run_c")
    repo.accept("run_d")
    check = data / "check"
    repo.clone_into(check)
    assert (check / "same.py").read_text() == "a\nb\n"


def test_continuing_merges_main_cleanly_when_possible(tmp_path: Path) -> None:
    data = tmp_path / "data"
    repo = ProjectRepo(data, "p")
    base = repo.ensure(None)
    for run_id, name in (("run_a", "a.py"), ("run_b", "b.py")):
        work = data / "tmp" / run_id
        repo.clone_into(work)
        _commit(work, name, "x\n")
        repo.submit(run_id, work, base)
    repo.accept("run_a")
    head, conflicts = repo.continue_into(data / "w", "run_b", "run_c")
    assert conflicts == [] and (data / "w" / "a.py").exists() and (data / "w" / "b.py").exists()


def test_a_crashed_run_gives_its_items_back(tmp_path: Path) -> None:
    from core.project_runs import release_crashed_run, selection
    from core.json_store import write_json
    from dataclasses import asdict

    data = tmp_path / "data"
    backlog = Backlog(data, "p")
    item = backlog.add("Feature", ["works"])
    taken = backlog.take_for_run("run_x", ids=[item.id])
    run_root = data / "runs" / "run_x"
    run_root.mkdir(parents=True)
    # No selection on disk: nothing is touched.
    assert release_crashed_run(data, "p", "run_x", run_root, "boom") == []
    write_json(run_root / "backlog_selection.json", [asdict(i) for i in taken])
    assert selection(run_root)

    released = release_crashed_run(data, "p", "run_x", run_root, "APIError: 409")
    assert [i.id for i in released] == [item.id]
    after = backlog.get(item.id)
    assert after.status == "open" and after.run_id is None
    assert "run errored: APIError: 409" in after.history[-1]["decision"]


def test_session_stats_are_recorded_and_averaged(tmp_path: Path) -> None:
    from core.project_runs import average_card_cost, record_session_stats, target_card_count

    assert average_card_cost(tmp_path, "p") is None
    done = lambda c: CardOutcome(title="t", column="done", cost_usd=c)  # noqa: E731
    record_session_stats(tmp_path, "p", "r1", [done(1.0), done(3.0), CardOutcome(
        title="x", column="needs_attention", cost_usd=2.0)])
    record_session_stats(tmp_path, "p", "r2", [done(2.0)])
    rows = (tmp_path / "projects/p/context/session_stats.jsonl").read_text().splitlines()
    assert json.loads(rows[0]) == {"run_id": "r1", "cards_done": 2, "cost_usd_done_cards": 4.0,
                                   "total_cost": 6.0}
    assert average_card_cost(tmp_path, "p") == 2.0          # (4 + 2) / 3
    assert target_card_count(10.0, 2.0) == 4 and target_card_count(1.0, 2.0) == 3
    assert target_card_count(None, 2.0) == 8


def test_close_run_records_stats_even_when_the_repo_step_fails(tmp_path: Path) -> None:
    from core.project_runs import average_card_cost, close_run

    close_run(tmp_path, "p", "r1", tmp_path / "nope", tmp_path / "nope",
              [CardOutcome(title="t", column="done", cost_usd=1.5)])
    assert average_card_cost(tmp_path, "p") == 1.5
