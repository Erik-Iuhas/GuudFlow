"""Tests for core.contract_measures: scoring contracts per session and proposing rules."""

from __future__ import annotations

import json
from pathlib import Path

from core.contract_measures import (
    RULES,
    add_project_rule,
    history,
    propose_into_draft,
    propose_rules,
    record_session,
    render_for_pm,
    session_values,
)
from core.contracts import load_for_project
from core.plan_drafts import PlanDrafts

TEAM = [{"role": "lead", "role_type": "architect", "contract": "tech_lead"},
        {"role": "sol", "role_type": "builder"},
        {"role": "rev", "is_reviewer": True}]


def _run(data_root: Path, run_id: str, *, drift: int = 0, reopened: int = 0,
         rounds: tuple[int, ...] = (0,)) -> None:
    root = data_root / "runs" / run_id
    root.mkdir(parents=True)
    board = [{"type": "DESIGN_DRIFT", "detail": json.dumps({"drift": ["a", "b"][:1]})}
             for _ in range(drift)]
    board += [{"type": "TASK_MOVED", "detail": json.dumps({"from": "done", "to": "review"})}
              for _ in range(reopened)]
    board.append({"type": "OVERSEER_INTERVENTION", "detail": json.dumps({"type": "split"})})
    (root / "board.jsonl").write_text("\n".join(json.dumps(r) for r in board) + "\n")
    (root / "retrospective.json").write_text(json.dumps({"cards": [
        {"task_id": f"T{i}", "column": "done", "review_round": r}
        for i, r in enumerate(rounds)]}))


LEDGER = {"R1.1": {"state": "verified"}, "R1.2": {"state": "claimed"},
          "R1.3": {"state": "unchecked"}, "R2.1": {"state": "person"}}


def test_session_values(tmp_path: Path) -> None:
    _run(tmp_path, "r1", drift=2, reopened=1, rounds=(1, 2))
    values = session_values(tmp_path / "runs" / "r1", LEDGER)
    assert values == {"interface_drift": 2.0, "plan_churn": 1.0, "reopened_cards": 1.0,
                      "review_rounds": 1.5, "verified_share": 0.33, "unverifiable_checks": 1.0}
    assert "verified_share" not in session_values(tmp_path / "runs" / "r1", None)


def test_record_scores_each_contract_on_the_team(tmp_path: Path) -> None:
    (tmp_path / "projects" / "p1").mkdir(parents=True)
    _run(tmp_path, "r1", drift=1)
    rows = record_session(tmp_path, "p1", "r1", TEAM, LEDGER)
    by = {r["contract"]: r for r in rows}
    # The reviewer's measure (escaped issues) needs the owner's findings: not scored here.
    assert set(by) == {"tech_lead", "builder"}
    assert by["tech_lead"]["roles"] == ["lead"] and "interface_drift" in by["tech_lead"]["values"]
    assert history(tmp_path, "p1") == rows
    assert "tech_lead: " in render_for_pm(tmp_path, "p1")


def test_rules_need_repeated_evidence_and_a_person(tmp_path: Path) -> None:
    (tmp_path / "projects" / "p1").mkdir(parents=True)
    for i, drift in enumerate((1, 0, 2), 1):
        _run(tmp_path, f"r{i}", drift=drift)
        record_session(tmp_path, "p1", f"r{i}", TEAM, LEDGER)
    proposals = {(p.contract, p.measure): p for p in propose_rules(tmp_path, "p1")}
    drift = proposals[("tech_lead", "interface_drift")]
    assert drift.evidence_runs == ("r1", "r3") and "2 of the last 3" in drift.reason()
    fresh = propose_into_draft(tmp_path, "p1")
    assert fresh and propose_into_draft(tmp_path, "p1") == []          # proposed once
    draft = PlanDrafts(tmp_path, "p1").current()
    assert draft is not None
    rule_changes = [c for c in draft.changes if c.change["op"] == "add_contract_rule"]
    assert rule_changes and all(c.accepted is None for c in rule_changes)
    # A person ticks it: the rule joins the project's contract and is not proposed again.
    assert add_project_rule(tmp_path, "p1", "tech_lead", RULES["interface_drift"])
    assert not add_project_rule(tmp_path, "p1", "tech_lead", RULES["interface_drift"])
    lead = load_for_project(tmp_path, "p1")["tech_lead"]
    assert lead.rules[-1] == RULES["interface_drift"] and len(lead.rules) > 1
    assert ("tech_lead", "interface_drift") not in {
        (p.contract, p.measure) for p in propose_rules(tmp_path, "p1")}
