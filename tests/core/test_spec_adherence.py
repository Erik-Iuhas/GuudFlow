"""Verdicts read back from a run's board: check states on accept, rule counts, adherence."""

from __future__ import annotations

import subprocess
from pathlib import Path

from core.backlog import Backlog, CardOutcome
from core.project_repo import ProjectRepo
from core.project_runs import decide_run
from core.spec import Spec
from core.spec_adherence import (
    adherence, check_id, check_verdicts, line_id, needs_cosign, read_board, reviews, rule_counts)
from core.work_db import WorkDB
from tests.spec_fixtures import RULE1, moved, review, seed_spec, write_board

R11 = "R1.1: a saved recipe shows in the list"
R12 = "R1.2: an empty title is refused"
R21 = "R2.1: a tag filters the list"
RL1 = f"rule-1: {RULE1}"


def _submit(data_root: Path, run_id: str = "run_a", project_id: str = "p1") -> ProjectRepo:
    repo = ProjectRepo(data_root, project_id)
    base = repo.ensure(None)
    work = data_root / "tmp" / run_id
    repo.clone_into(work)
    (work / "a.py").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: a"], cwd=work, check=True)
    repo.submit(run_id, work, base)
    return repo


def test_line_ids() -> None:
    assert line_id("R4.2: the list is sorted") == "R4.2"
    assert line_id("  r4.2 : x") == "R4.2"
    assert line_id("rule-12: no floats") == "rule-12"
    assert line_id("RULE-2: x") == "rule-2"
    assert line_id("the list is sorted") is None and line_id("R4: whole requirement") is None
    assert check_id("R10.3: a") == "R10.3" and check_id("rule-1: a") is None


def test_reviews_read_from_the_board(tmp_path: Path) -> None:
    root = write_board(tmp_path, "r1", [
        review("T1", [(R11, False), (RL1, True), ("a free line", True)]),
        {"type": "THINKING", "from_agent": "x", "detail": None},
        review("T1", [(R11, True), (RL1, True)])])
    found = reviews(read_board(root))
    assert [r.lines for r in found] == [((("R1.1", False)), ("rule-1", True)),
                                        (("R1.1", True), ("rule-1", True))]
    assert check_verdicts(found) == {"R1.1": True}              # the latest wins
    assert rule_counts(found) == {"rule-1": {"met": 2, "unmet": 0}}
    assert read_board(tmp_path / "ghost") == []


def test_check_states_are_set_on_accept_and_the_latest_verdict_wins(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    repo = _submit(tmp_path)
    write_board(tmp_path, "run_a", [
        review("T1", [(R11, False), (R12, True), (RL1, False)]),
        review("T1", [(R11, True), (R12, True), (RL1, True)]),     # R1.1 fixed later
        review("T2", [(R21, False), (RL1, True)])])                # R2.1 stays unmet
    before = Spec(WorkDB(tmp_path / "projects" / "p1").read_spec_doc())
    assert before.check("R1.1").state == "unknown"

    decide_run(tmp_path, "p1", "run_a", accept=True)

    after = Spec(WorkDB(tmp_path / "projects" / "p1").read_spec_doc())
    assert (after.check("R1.1").state, after.check("R1.1").run_id) == ("met", "run_a")
    assert after.check("R1.2").state == "met"
    assert (after.check("R2.1").state, after.check("R2.1").run_id) == ("unmet", "run_a")
    # spec-version neutral, and the change is in the history
    assert after.version == before.version == 3
    assert after.requirement("R1").version == 1
    note = after.requirement("R1").history[-1]
    assert note["note"] == "set by session" and note["by"] == "session:run_a"
    assert note["check_states"] == {"R1.1": "met", "R1.2": "met"}
    # rule verdicts per run on the run state row
    assert repo.get("run_a").rule_verdicts == {"rule-1": {"met": 2, "unmet": 1}}  # type: ignore[union-attr]


def test_a_later_session_overrides_the_earlier_state(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    _submit(tmp_path, "run_a")
    write_board(tmp_path, "run_a", [review("T1", [(R11, True)])])
    decide_run(tmp_path, "p1", "run_a", accept=True)
    repo = ProjectRepo(tmp_path, "p1")
    work = tmp_path / "tmp" / "run_b"
    repo.clone_into(work)
    (work / "b.py").write_text("y\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: b"], cwd=work, check=True)
    repo.submit("run_b", work, repo.sha("main"))
    write_board(tmp_path, "run_b", [review("T1", [(R11, False)])])
    decide_run(tmp_path, "p1", "run_b", accept=True)
    check = Spec(WorkDB(tmp_path / "projects" / "p1").read_spec_doc()).check("R1.1")
    assert (check.state, check.run_id) == ("unmet", "run_b")


def test_a_rejected_run_sets_no_states(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    _submit(tmp_path)
    write_board(tmp_path, "run_a", [review("T1", [(R11, True)])])
    decide_run(tmp_path, "p1", "run_a", accept=False)
    assert Spec(WorkDB(tmp_path / "projects" / "p1").read_spec_doc()).check("R1.1").state \
        == "unknown"


def test_accept_without_a_board_or_spec_still_works(tmp_path: Path) -> None:
    _submit(tmp_path)                                           # no board file, no spec
    assert decide_run(tmp_path, "p1", "run_a", accept=True).run.state == "accepted"


def test_adherence_counts(tmp_path: Path) -> None:
    rows = [review("T1", [(R11, True), (RL1, False)]),
            review("T1", [(R11, True), (RL1, True)]),           # fixed in round 2
            moved("T1", "done"),
            review("T2", [(RL1, False)]),                       # never fixed, card not done
            moved("T2", "needs_attention"),
            review("T3", [(RL1, True)]), moved("T3", "done")]
    result = adherence(rows)
    assert result == {"rule_verdicts": {"rule-1": {"met": 2, "unmet": 2}},
                      "unmet": 1, "fixed": 1, "unmet_merged": 0}
    # a card that ended done with an unmet rule would show up
    assert adherence([review("T9", [(RL1, False)]), moved("T9", "done")])["unmet_merged"] == 1


def test_needs_cosign_matrix(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    backlog = Backlog(tmp_path, "p1")
    should_item = backlog.add("Tags", [R21], serves=["R2"])
    must_item = backlog.add("Save", [R11], serves=["R1"])
    backlog.take_for_run("run_s", ids=[should_item.id])
    backlog.take_for_run("run_m", ids=[must_item.id])
    for seat, run, expected in [("off", "run_m", False), ("all", "run_s", True),
                                ("all", "run_m", True), ("must", "run_s", False),
                                ("must", "run_m", True), ("must", "run_none", False)]:
        assert needs_cosign(tmp_path, "p1", run, seat) is expected, (seat, run)
    backlog.record_outcomes("run_m", [CardOutcome("Save", "done", must_item.id)])
    backlog.decide("run_m", accepted=True)
    assert needs_cosign(tmp_path, "p1", "run_m", "must") is False   # decided: nothing to sign


def test_the_ledger_decides_states_at_acceptance(tmp_path: Path) -> None:
    from core.spec_adherence import ledger_for_run
    seed_spec(tmp_path)
    with WorkDB(tmp_path / "projects" / "p1").spec_doc() as doc:
        for r in doc["requirements"]:
            for c in r["checks"]:
                if c["id"] == "R1.1":
                    c["test"], c["method"] = "tests/list.test.js::shows saved", "unit"
                if c["id"] == "R1.2":
                    c["method"] = "manual"
    repo = _submit(tmp_path)
    Backlog(tmp_path, "p1").add("Recipes", ["R1.1", "R1.2"], serves=["R1"])
    item = Backlog(tmp_path, "p1").items()[0]
    Backlog(tmp_path, "p1").take_for_run("run_a", [item.id])
    write_board(tmp_path, "run_a", [review("T1", [(R11, True), (R12, True)])])
    ledger = ledger_for_run(tmp_path, "p1", "run_a",
                            {"tests/list.test.js::shows saved": "failed"})
    assert ledger["R1.1"]["state"] == "failing"          # the reviewer said met; the test says no
    assert ledger["R1.2"]["state"] == "claimed"
    repo.record_check("run_a", {"command": "bun test", "status": "failed", "ledger": ledger})

    decide_run(tmp_path, "p1", "run_a", accept=True, confirmed_checks=["R1.2"])

    spec = Spec(WorkDB(tmp_path / "projects" / "p1").read_spec_doc())
    assert (spec.check("R1.1").state, spec.check("R1.1").evidence) == ("unmet", "failing")
    assert (spec.check("R1.2").state, spec.check("R1.2").evidence) == ("met", "person")
    stored = repo.get("run_a").check["ledger"]  # type: ignore[union-attr,index]
    assert stored["R1.2"]["state"] == "person"


def test_without_a_ledger_reviewer_verdicts_are_claims(tmp_path: Path) -> None:
    seed_spec(tmp_path)
    _submit(tmp_path)
    write_board(tmp_path, "run_a", [review("T1", [(R11, True), (R21, False)])])
    decide_run(tmp_path, "p1", "run_a", accept=True)
    spec = Spec(WorkDB(tmp_path / "projects" / "p1").read_spec_doc())
    assert (spec.check("R1.1").state, spec.check("R1.1").evidence) == ("met", "claimed")
    assert (spec.check("R2.1").state, spec.check("R2.1").evidence) == ("unmet", "unmet")
