"""Tests for core.harness.retrospective (plan §11.4, O20)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from core.harness.retrospective import (
    build_retrospective,
    carry_forward,
    finalize_run,
    load_prior_carry_forward,
    playbook_diff,
)
from core.playbook import Playbook


def _row(kind: str, who: str, summary: str = "", detail: Any = None,
         ts: str = "2026-10-04T10:00:00+00:00") -> dict[str, Any]:
    return {"type": kind, "from_agent": who, "summary": summary,
            "detail": json.dumps(detail) if isinstance(detail, dict) else detail, "timestamp": ts}


def _merge(a: str, b: str) -> dict[str, Any]:
    return {"kind": "note", "author": "api", "text": "merged", "data": {"merge": [a, b]}}


def _issue(item: str) -> dict[str, Any]:
    return {"kind": "review_issue", "author": "rev", "text": f"{item}: wrong", "data": {"item": item}}


def _run_dir(tmp_path: Path, *, t2_done: bool = False) -> Path:
    run = tmp_path / "runs" / "run_a"
    repo = run / "workspace" / "working"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "client.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
                   cwd=repo, check=True)
    board = [
        _row("COMPLETE", "planner", ts="2026-10-04T10:00:00+00:00"),
        _row("REJECTED", "rev", detail={"task_id": "T2", "authors": ["api"], "round": 2}),
        _row("OVERSEER_INTERVENTION", "overseer", "rebrief api",
             {"type": "rebrief", "role": "api", "task_id": "T2",
              "addendum": "Return parsed models, not raw dicts.", "rationale": "x" * 20}),
        _row("TASK_MOVED", "api", detail={"task_id": "T1", "to": "done"},
             ts="2026-10-04T10:40:00+00:00"),
        _row("COMPLETE", "api", ts="2026-10-04T10:41:00+00:00"),
    ]
    (run / "board.jsonl").write_text("\n".join(json.dumps(r) for r in board) + "\nnot json\n")
    t1 = {"task_id": "T1", "story_id": "plan", "assigned_to": "api", "title": "Transport",
          "column": "todo", "acceptance_criteria": ["get works"], "thread": []}
    t1_done = {**t1, "column": "done", "thread": [_merge("a" * 40, "b" * 40)]}
    t2 = {"task_id": "T2", "story_id": "plan", "assigned_to": "api", "title": "Models",
          "column": "done" if t2_done else "in_progress", "review_round": 2,
          "acceptance_criteria": ["parse forecast"],
          "thread": [_merge("b" * 40, "c" * 40), _issue("parse forecast"),
                     _merge("c" * 40, "d" * 40), _issue("parse forecast"),
                     {"kind": "decision", "author": "overseer", "text": "use dataclasses"}]}
    legacy = {"task_id": "abc", "story_id": "", "assigned_to": "api", "column": "todo"}
    (run / "tasks.jsonl").write_text("\n".join(json.dumps(r) for r in (t1, t1_done, t2, legacy)))
    costs = [{"role": "api", "cost_usd": 0.5}, {"role": "rev", "cost_usd": 0.25},
             {"role": "api", "cost_usd": 0.25}]
    (run / "llm_costs.jsonl").write_text("\n".join(json.dumps(c) for c in costs))
    return run


def test_retrospective_reads_the_run(tmp_path: Path) -> None:
    retro = build_retrospective(_run_dir(tmp_path), "run_a")
    assert retro.cost_by_role == {"api": 0.75, "rev": 0.25} and retro.total_cost == 1.0
    assert {c.task_id: c.column for c in retro.cards} == {"T1": "done", "T2": "in_progress"}
    assert retro.status == "incomplete"
    assert retro.recurring_issues == [("T2", "parse forecast")]
    assert retro.review_rounds_used == {"api->rev": 2}
    assert retro.stall_minutes > 20                      # 40 min gap, minus 10 allowed
    assert [(i.type, i.target, i.worked) for i in retro.interventions] == [("rebrief", "T2", False)]
    assert retro.metrics()["cards_done"] == 1.0
    assert next(c for c in retro.cards if c.task_id == "T1").commits == ["b" * 40]


def test_playbook_diff_is_evidence_backed_and_reinforces(tmp_path: Path) -> None:
    retro = build_retrospective(_run_dir(tmp_path, t2_done=True), "run_a")
    playbook = Playbook("p", path=tmp_path / "pb.json")
    diff = playbook_diff(retro, playbook)
    sections = sorted(e.section for e in diff.added)
    assert sections == ["brief_addenda", "known_pitfalls", "task_templates"]
    assert all(e.evidence_runs == ["run_a"] for e in diff.added)
    diff.apply(playbook, "run_a")
    again = playbook_diff(retro, playbook)
    assert not again.added and len(again.reinforced) == 3


def test_carry_forward_attributes_done_work_and_keeps_open_cards(tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    cf = carry_forward(build_retrospective(run, "run_a"), run)
    assert cf["done"][0]["task_id"] == "T1" and cf["done"][0]["commit"] == "b" * 10
    assert [c["task_id"] for c in cf["open_cards"]] == ["T2"]
    assert "previous run, commit" in cf["text"] and "open issue: parse forecast" in cf["text"]
    assert "client.py" in cf["files"] and "use dataclasses" in cf["text"]


def test_finalize_writes_files_and_updates_playbook(tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    retro = finalize_run(run, "run_a", "proj", tmp_path / "data")
    assert retro is not None
    for name in ("retrospective.json", "carry_forward.json", "carry_forward.md"):
        assert (run / name).is_file(), name
    playbook = Playbook.load(tmp_path / "data", "proj")
    assert "run_a" in json.dumps(playbook.metrics_history())
    assert playbook.entries()


def test_finalize_never_raises(tmp_path: Path) -> None:
    assert finalize_run(tmp_path / "missing", "r", None, None) is None   # logged, not raised


def test_load_prior_carry_forward(tmp_path: Path) -> None:
    prior = _run_dir(tmp_path)
    finalize_run(prior, "run_a", None, None)
    new = tmp_path / "runs" / "run_b"
    (new / "workspace" / "inputs" / "from_run_run_a").mkdir(parents=True)
    docs = load_prior_carry_forward(new)
    assert len(docs) == 1 and docs[0]["run_id"] == "run_a"
    assert load_prior_carry_forward(tmp_path / "nowhere") == []
