"""The project record: trace, export and the files kept in the repo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.backlog import Backlog, CardOutcome
from core.plan_drafts import PlanDrafts, PlanTurn
from core.project_repo import ProjectRepo
from core.record import RecordError, export, spec_files, trace
from tests.spec_fixtures import with_reasons

REASON = "because the team already runs it"


def _turn(*changes: dict[str, Any]) -> PlanTurn:
    return PlanTurn.model_validate(with_reasons({"reply": "ok", "changes": list(changes)}))


def _approve(pd: PlanDrafts, *changes: dict[str, Any], who: str = "erik") -> dict[str, str]:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(*changes), False)
    for c in d.changes:
        pd.set_change(d.id, c.cid, True)
    return pd.approve(d.id, who).created


def _commit(work: Path, name: str, text: str) -> None:
    (work / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", f"feat: add {name}"], cwd=work, check=True)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """R1 (edited once), D1 serving it, B1 done in an accepted session, B2 in a rejected one."""
    pd = PlanDrafts(tmp_path, "p")
    pd.add_message("human", "I want people to sign in")
    first = pd.open_draft().id
    pd.add_message("pm", "Proposing a sign-in requirement and a storage decision", draft_id=first)
    _approve(pd,
             {"op": "add_requirement", "ref": "new:1", "text": "Users can sign in",
              "kind": "feature", "priority": "must", "checks": ["login works"]},
             {"op": "add_decision", "ref": "new:2", "title": "Database choice",
              "choice": "SQLite", "reason": REASON, "serves": ["new:1"], "binding": True},
             {"op": "add_item", "ref": "new:3", "title": "Build login", "serves": ["new:1"]})
    pd.add_message("human", "also lock the account after 5 tries")
    second = pd.open_draft().id
    pd.add_message("pm", "Adding a lockout check", draft_id=second)
    _approve(pd, {"op": "edit_requirement", "id": "R1", "text": "Users can sign in safely",
                  "checks": ["login works", "lockout after 5 tries"]}, who="dana")
    _approve(pd, {"op": "add_item", "ref": "new:1", "title": "Lockout", "serves": ["R1"]})

    backlog = Backlog(tmp_path, "p")
    repo = ProjectRepo(tmp_path, "p")
    base = repo.ensure(None)
    for run_id, name in (("run_a", "login.py"), ("run_b", "lock.py")):
        work = tmp_path / "tmp" / run_id
        repo.clone_into(work)
        _commit(work, name, "x\n")
        repo.submit(run_id, work, base)
    backlog.take_for_run("run_a", ids=["B1"])
    backlog.record_outcomes("run_a", [CardOutcome("Login", "done", "B1", cost_usd=0.5,
                                                  summary="login built")])
    backlog.take_for_run("run_b", ids=["B2"])
    backlog.record_outcomes("run_b", [CardOutcome("Lock", "not_done", "B2")])
    repo.accept("run_a", "looks good")
    backlog.decide("run_a", True, "looks good")
    repo.reject("run_b", "no lockout test")
    backlog.decide("run_b", False, "no lockout test")
    with pd.db.spec_doc() as doc:
        checks = doc["requirements"][0]["checks"]
        checks[0].update(state="met", run_id="run_a")
        checks[1].update(state="unmet", run_id="run_b")
    return tmp_path


def _kinds(events: list[Any]) -> list[str]:
    return [e.kind for e in events]


def test_requirement_trace_is_sorted_and_complete(project: Path) -> None:
    events = trace(project, "p", "R1")
    assert [e.at for e in events] == sorted(e.at for e in events)
    assert events[-1].kind == "status" and "1/2 checks met" in events[-1].text
    texts = "\n".join(e.text for e in events)
    assert "R1 v1 added" in texts and "R1 v2" in texts and "lockout after 5 tries" in texts
    assert "I want people to sign in" in texts and "Adding a lockout check" in texts
    assert "D1 serves R1" in texts
    assert "R1.1 met in session run_a" in texts and "R1.2 unmet in session run_b" in texts
    assert "Session run_a accepted: looks good" in texts
    assert "Session run_b rejected: no lockout test" in texts
    assert {"spec", "plan", "decision", "work", "session", "review", "status"} <= set(_kinds(events))
    v2 = next(e for e in events if e.text.startswith("R1 v2"))
    assert v2.by == "dana" and "draft-2" in v2.links
    assert "login.py" in events[-1].text
    first = next(e for e in events if e.text.startswith("R1 v1"))
    assert first.by == "erik" and "Users can sign in" in first.text
    # the human message comes before the PM's one
    plan = [e for e in events if e.kind == "plan"]
    assert plan[0].by == "human" and plan[1].by == "pm"
    assert "B1" in next(e for e in events if e.text.startswith("Session run_a submitted")).links


def test_check_trace_keeps_only_its_review(project: Path) -> None:
    events = trace(project, "p", "R1.2")
    reviews = [e for e in events if e.kind == "review"]
    assert len(reviews) == 1 and "R1.2 unmet" in reviews[0].text
    assert events[-1].text.startswith("R1.2 is unmet")


def test_decision_item_session_traces(project: Path) -> None:
    dec = trace(project, "p", "D1")
    assert "D1 accepted (pm)" in dec[0].text or "D1 accepted" in "\n".join(e.text for e in dec)
    assert any("became rule-1" in e.text for e in dec)
    assert any(e.kind == "work" and "B1 added" in e.text for e in dec)
    assert "binding" in dec[-1].text and "B1 (done)" in dec[-1].text

    b = trace(project, "p", "B1")
    assert any("B1 in session run_a: done" in e.text and "login built" in e.text for e in b)
    assert any(e.kind == "session" and e.text.startswith("commit ") for e in b)
    assert b[-1].text.startswith("B1 is done")

    run = trace(project, "p", "run_b")
    assert any("rejected: no lockout test" in e.text for e in run)
    assert any("lock.py" in e.text for e in run) and run[-1].text.startswith("Session run_b is rejected")


def test_file_trace_uses_git_log(project: Path) -> None:
    events = trace(project, "p", "login.py")
    assert any("Session run_a changed login.py (accepted)" in e.text for e in events)
    assert not any("run_b" in e.text for e in events)
    assert events[-1].text == "login.py was changed by 1 session(s): run_a"
    assert trace(project, "p", "nothing.py")[-1].text.startswith("nothing.py was changed by 0")


def test_unknown_refs_are_refused(project: Path) -> None:
    for bad in ("R9", "D9", "B9", "R1.9", "", "../etc/passwd"):
        with pytest.raises(RecordError):
            trace(project, "p", bad)


def test_trace_of_empty_project(tmp_path: Path) -> None:
    assert trace(tmp_path, "p", "some/file.txt")[-1].kind == "status"


def test_export_markdown_and_json(project: Path) -> None:
    md = export(project, "p", include={"spec", "decisions", "sessions"}, fmt="md")
    assert "## Requirements" in md and "### R1. Users can sign in safely" in md
    assert "- [x] R1.1 login works" in md and "- [ ] R1.2 lockout after 5 tries (unmet, session run_b)" in md
    assert "History:" in md and "text \"Users can sign in\" → \"Users can sign in safely\"" in md
    assert "### D1. Database choice" in md and "## Sessions and reviews" in md
    assert "run_a accepted" in md and "## Plan conversations" not in md and "## Costs" not in md

    full = export(project, "p", include={"spec", "decisions", "sessions", "plan", "rules", "costs"})
    assert "## Plan conversations" in full and "I want people to sign in" in full
    assert "## Rules" in full and "Database choice: SQLite" in full
    assert "Session run_a: $0.50" in full and "Total: $0.50" in full

    data = json.loads(export(project, "p", include={"spec", "costs", "sessions"}, fmt="json"))
    assert data["requirements"][0]["history"] and "decisions" not in data
    assert data["costs"]["sessions"] == {"run_a": 0.5, "run_b": 0.0}
    assert {s["run_id"] for s in data["sessions"]} == {"run_a", "run_b"}
    with pytest.raises(RecordError):
        export(project, "p", include={"nope"})


def test_spec_files_and_adr_naming(project: Path) -> None:
    pd = PlanDrafts(project, "p")
    _approve(pd, {"op": "add_decision", "ref": "new:1", "title": "Use Postgres, not SQLite!",
                  "choice": "Postgres", "reason": REASON, "serves": ["R1"]})
    files = spec_files(project, "p")
    assert set(files) == {"SPEC.md", "docs/decisions/0001-database-choice.md",
                          "docs/decisions/0002-use-postgres-not-sqlite.md"}
    spec = files["SPEC.md"]
    assert "- [x] R1.1 login works" in spec and "- [ ] R1.2" in spec
    assert "[D1](docs/decisions/0001-database-choice.md)" in spec
    adr = files["docs/decisions/0001-database-choice.md"]
    assert adr.startswith("# D1. Database choice")
    for part in ("Status: accepted (binding)", "Proposed by: a person",
                 "Approved by: erik", "## Context", "- R1: Users can sign in safely",
                 "## Decision", "SQLite", "## Consequences", REASON, "## Serves", "- R1",
                 "## Evidence", "Session run_a: accepted, looks good"):
        assert part in adr
    assert "No session has worked on it yet" in files["docs/decisions/0002-use-postgres-not-sqlite.md"]
    assert files == spec_files(project, "p")


def test_empty_project_files(tmp_path: Path) -> None:
    assert spec_files(tmp_path, "p") == {"SPEC.md": spec_files(tmp_path, "p")["SPEC.md"]}
    assert "No requirements yet." in spec_files(tmp_path, "p")["SPEC.md"]
