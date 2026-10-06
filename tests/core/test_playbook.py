"""Tests for core/playbook.py (S22)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.playbook import (
    MAX_RUN_METRICS,
    Playbook,
    PlaybookDiff,
    PlaybookEntry,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def pb(tmp_path: Path) -> Playbook:
    return Playbook.load(tmp_path, "proj1")


def test_add(pb: Playbook) -> None:
    e = pb.add("brief_addenda", "builder", "Read notes first", "run_1", now=T0)
    assert e.state == "emerging" and e.confidence == 0.5
    assert e.evidence_runs == ["run_1"] and len(e.id) == 12
    assert pb.entries() == [e]


def test_add_duplicate_reinforces(pb: Playbook) -> None:
    a = pb.add("known_pitfalls", "", "hourly arrays", "run_1", now=T0)
    b = pb.add("known_pitfalls", "", "hourly arrays", "run_2", now=T0)
    assert a.id == b.id and len(pb.entries()) == 1
    assert b.confidence == pytest.approx(0.55)
    assert b.evidence_runs == ["run_1", "run_2"]


def test_reinforce_promotes_to_active(pb: Playbook) -> None:
    e = pb.add("review_rounds", "builder->reviewer", 3, "r1", confidence=0.55, now=T0)
    pb.reinforce(e.id, "r2", now=T0)
    assert e.confidence == pytest.approx(0.6) and e.state == "active"
    pb.reinforce(e.id, "r2", now=T0)  # same run not double-counted as evidence
    assert e.evidence_runs == ["r1", "r2"]


def test_contradict_decay_archive(pb: Playbook) -> None:
    e = pb.add("team_adjustments", "", {"split": "backend"}, "r1", confidence=0.7, now=T0)
    assert e.state == "active"
    pb.contradict(e.id, "r2", now=T0)
    assert e.confidence == pytest.approx(0.55) and e.state == "active"
    assert not e.needs_review and e.contradicted_runs == ["r2"]
    pb.contradict(e.id, "r3", now=T0)  # 0.40 -> flagged, not below 0.4, still active
    assert e.needs_review and e.state == "active"
    pb.contradict(e.id, "r4", now=T0)  # 0.25 -> declining
    assert e.state == "declining"
    pb.contradict(e.id, "r5", now=T0)  # 0.10 -> archived
    assert e.state == "archived"
    assert pb.entries() == []
    assert pb.entries(include_archived=True) == [e]
    pb.reinforce(e.id, "r6", now=T0)
    assert e.state == "archived"  # terminal


def test_recency_decay(pb: Playbook) -> None:
    e = pb.add("brief_addenda", "builder", "x", "r1", confidence=0.8, now=T0)
    pb.decay(T0 + timedelta(days=30))  # 0.8*exp(-1/3)=0.573, still active
    assert e.confidence == pytest.approx(0.573, abs=1e-3) and e.state == "active"
    assert pb.decay(T0 + timedelta(days=30)) == []  # idempotent
    assert e.confidence == pytest.approx(0.573, abs=1e-3)
    pb.decay(T0 + timedelta(days=60))  # 0.41 -> hysteresis keeps active
    assert e.state == "active"
    assert pb.decay(T0 + timedelta(days=80)) == [e.id]  # 0.329 -> declining
    assert e.state == "declining"
    assert pb.decay(T0 + timedelta(days=300)) == [e.id]
    assert e.state == "archived"


def test_reinforce_after_decay_uses_decayed_confidence(pb: Playbook) -> None:
    e = pb.add("brief_addenda", "b", "x", "r1", confidence=0.8, now=T0)
    pb.reinforce(e.id, "r2", now=T0 + timedelta(days=90))
    assert e.confidence == pytest.approx(0.8 * 0.3679 + 0.05, abs=1e-3)


def test_evidence_required() -> None:
    with pytest.raises(ValueError):
        PlaybookEntry(id="", section="known_pitfalls", key="", value="x", evidence_runs=[])
    with pytest.raises(ValueError):
        PlaybookEntry.create("known_pitfalls", "", "x", "")
    pb = Playbook("p")
    with pytest.raises(ValueError):
        pb.add("known_pitfalls", "", "x", "")
    with pytest.raises(ValueError):
        PlaybookEntry.create("bogus", "", "x", "r1")  # type: ignore[arg-type]


def test_load_save_round_trip(tmp_path: Path) -> None:
    empty = Playbook.load(tmp_path, "proj1")
    assert empty.entries() == [] and empty.metrics_history() == []
    assert not (tmp_path / "projects/proj1/playbook.json").exists()
    empty.add("task_templates", "web-app", ["scaffold", "api"], "r1", now=T0)
    empty.add("capability_hints", "tester", {"browser": "screenshot"}, "r1", now=T0)
    empty.record_run_metrics("r1", {"stall_minutes": 4.0})
    empty.save()
    path = tmp_path / "projects/proj1/playbook.json"
    assert path.exists() and not list(path.parent.glob("*.tmp"))
    json.loads(path.read_text())
    again = Playbook.load(tmp_path, "proj1")
    assert again.to_json() == empty.to_json()
    assert Playbook.from_json(again.to_json()).to_json() == again.to_json()


def test_load_rejects_entry_without_evidence(tmp_path: Path) -> None:
    p = tmp_path / "projects/p/playbook.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"entries": [
        {"section": "known_pitfalls", "key": "", "value": "x", "evidence_runs": []}
    ]}))
    with pytest.raises(ValueError):
        Playbook.load(tmp_path, "p")


def test_bad_project_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Playbook.load(tmp_path, "../evil")


def test_active_for_role(pb: Playbook) -> None:
    a = pb.add("brief_addenda", "builder", "A", "r1", confidence=0.7, now=T0)
    pb.add("brief_addenda", "builder", "B", "r1", confidence=0.5, now=T0)
    pb.add("brief_addenda", "tester", "other role", "r1", now=T0)
    old = pb.add("brief_addenda", "builder", "dead", "r1", confidence=0.5, now=T0)
    for i in range(3):
        pb.contradict(old.id, f"c{i}", now=T0)
    assert old.state == "archived"
    pb.add("capability_hints", "builder", {"browser": "none", "lint": "on"}, "r1", confidence=0.5, now=T0)
    pb.add("capability_hints", "builder", {"browser": "screenshot"}, "r1", confidence=0.8, now=T0)
    got = pb.active_for_role("builder")
    assert got["brief_addenda"] == ["A", "B"] and a.state == "active"
    assert got["capability_hints"] == {"browser": "screenshot", "lint": "on"}
    assert pb.active_for_role("nobody") == {"brief_addenda": [], "capability_hints": {}}


def test_render_for_meta_ordering(pb: Playbook) -> None:
    assert pb.render_for_meta() == ""
    pb.add("known_pitfalls", "", "low-conf", "r1", confidence=0.65, now=T0)
    hi = pb.add("known_pitfalls", "", "high-conf", "r1", confidence=0.9, now=T0)
    pb.reinforce(hi.id, "r2", now=T0)
    pb.add("known_pitfalls", "", "still-emerging", "r1", confidence=0.5, now=T0)
    pb.add("review_rounds", "builder->reviewer", 3, "r1", confidence=0.7, now=T0)
    out = pb.render_for_meta()
    assert out.index("high-conf") < out.index("low-conf")
    assert "still-emerging" not in out
    assert "builder->reviewer: 3" in out and "2 runs" in out
    assert out.index("known_pitfalls") < out.index("review_rounds")


def test_diff_apply(pb: Playbook) -> None:
    keep = pb.add("known_pitfalls", "", "keep", "r1", now=T0)
    drop = pb.add("known_pitfalls", "", "drop", "r1", now=T0)
    diff = PlaybookDiff(
        added=[PlaybookEntry.create("brief_addenda", "builder", "new", "ignored", 0.6)],
        reinforced=[keep.id],
        contradicted=[drop.id],
    )
    touched = diff.apply(pb, "r2")
    assert len(touched) == 3
    new = pb.entries("brief_addenda")[0]
    assert new.evidence_runs == ["r2"] and new.confidence == 0.6
    assert "r2" in keep.evidence_runs and drop.contradicted_runs == ["r2"]
    with pytest.raises(KeyError):
        PlaybookDiff(reinforced=["nope"]).apply(pb, "r3")


def test_metrics_cap(pb: Playbook) -> None:
    for i in range(MAX_RUN_METRICS + 5):
        pb.record_run_metrics(f"r{i}", {"stall_minutes": float(i), "interventions": 1.0})
    hist = pb.metrics_history()
    assert len(hist) == MAX_RUN_METRICS
    assert hist[0]["run_id"] == "r5" and hist[-1]["run_id"] == f"r{MAX_RUN_METRICS + 4}"
    pb.record_run_metrics("r10", {"stall_minutes": 99.0})  # replaces, moves to end
    assert pb.metrics_history()[-1]["metrics"] == {"stall_minutes": 99.0}
    assert len(pb.metrics_history()) == MAX_RUN_METRICS
