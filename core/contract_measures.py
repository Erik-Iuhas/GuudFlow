"""Contract measures and rule proposals (role contracts plan §6, RC9).

Each contract declares `measures`. When a session is accepted, `record_session` scores the
measures it can from what the session left (the board, the retrospective, the verification
ledger) and appends one row per contract on the team to
`projects/{id}/contract_measures.jsonl`. Nothing is self-reported: an agent never scores itself.

`propose_rules` reads the recent rows. When one measure was bad in at least `MIN_EVIDENCE` of the
last `WINDOW` sessions, it proposes a rule for that contract (a fixed text per measure), backed by
those sessions. A proposal is a `add_contract_rule` change in the plan draft: a person ticks it,
and approving appends the rule to the project's contract override
(`projects/{id}/contracts/{contract}.yaml`) so it reaches the role's briefs from the next session.
Rules 16 and 22: evidence runs, and nothing applies without a human.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.contracts import Contract, contract_id_for, load_for_project

log = logging.getLogger(__name__)

MEASURES_FILE = "contract_measures.jsonl"
WINDOW = 3                 # sessions looked at
MIN_EVIDENCE = 2           # bad sessions needed for a proposal

# measure → (bad when value is above or below the threshold, threshold)
THRESHOLDS: dict[str, tuple[str, float]] = {
    "interface_drift": ("above", 0),
    "plan_churn": ("above", 2),
    "verified_share": ("below", 0.5),
    "review_rounds": ("above", 1.5),
    "reopened_cards": ("above", 0),
    "unverifiable_checks": ("above", 0),
}

# The rule proposed when a measure keeps being bad, per measure.
RULES: dict[str, str] = {
    "interface_drift": "Before you submit, compare what your files export with the interfaces "
                       "your card implements; a new public name goes through the tech lead "
                       "as a design change first.",
    "plan_churn": "Size cards so each one owns one component and fits one turn; split work "
                  "while planning, not during the session.",
    "verified_share": "Every unit check a card serves gets a test whose name contains the check "
                      "id (for example `R3.2`), so the ledger can verify it.",
    "review_rounds": "Run the check command and walk the reviewer's checklist yourself before "
                     "you submit.",
    "reopened_cards": "Pass a card only when every acceptance line has evidence you checked "
                      "yourself in this review.",
    "unverifiable_checks": "Every check states how it is verified: a test, a command, or manual "
                           "steps a person can follow.",
}


@dataclass(frozen=True)
class RuleProposal:
    """A rule proposed for a contract, with the sessions that support it."""

    contract: str
    measure: str
    rule: str
    evidence_runs: tuple[str, ...]
    values: tuple[float, ...]

    def reason(self) -> str:
        """The draft change's reason, in plain words."""
        shown = ", ".join(f"{r}: {v:g}" for r, v in zip(self.evidence_runs, self.values))
        way, limit = THRESHOLDS[self.measure]
        return (f"{self.measure} was {way} {limit:g} in {len(self.evidence_runs)} of the last "
                f"{WINDOW} sessions ({shown})")


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


def _detail(row: Mapping[str, Any]) -> dict[str, Any]:  # Any: signal JSON
    try:
        value = json.loads(row.get("detail") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def session_values(run_root: Path,
                   ledger: Mapping[str, Mapping[str, Any]] | None) -> dict[str, float]:  # Any: JSON
    """The measures one session yields, from its board, retrospective and ledger. A measure the
    session gives no data for is left out (no ledger: no verified_share)."""
    board = _jsonl(Path(run_root) / "board.jsonl")
    out: dict[str, float] = {}
    drift = [r for r in board if r.get("type") == "DESIGN_DRIFT"]
    out["interface_drift"] = float(sum(len(_detail(r).get("drift") or [1]) for r in drift))
    out["plan_churn"] = float(sum(
        1 for r in board if r.get("type") == "OVERSEER_INTERVENTION"
        and _detail(r).get("type") in ("split", "reassign")))
    out["reopened_cards"] = float(sum(
        1 for r in board if r.get("type") == "TASK_MOVED"
        and _detail(r).get("from") == "done" and _detail(r).get("to") != "done"))
    retro_path = Path(run_root) / "retrospective.json"
    try:
        retro = json.loads(retro_path.read_text(encoding="utf-8")) if retro_path.is_file() else {}
    except (OSError, ValueError):
        retro = {}
    done = [c for c in retro.get("cards") or [] if isinstance(c, dict)
            and c.get("column") == "done"]
    if done:
        out["review_rounds"] = round(sum(int(c.get("review_round") or 0) for c in done)
                                     / len(done), 2)
    if ledger:
        entries = [e for e in ledger.values() if isinstance(e, Mapping)]
        units = [e for e in entries if e.get("state") != "person"]
        if units:
            out["verified_share"] = round(sum(e.get("state") == "verified" for e in units)
                                          / len(units), 2)
        out["unverifiable_checks"] = float(sum(e.get("state") == "unchecked" for e in entries))
    return out


def _team_contracts(team: Sequence[Mapping[str, Any]],  # Any: agent rows
                    contracts: Mapping[str, Contract]) -> dict[str, list[str]]:
    """contract id → the roles that work under it."""
    from core.harness.capabilities import infer_role_type
    out: dict[str, list[str]] = {}
    for row in team:
        role = str(row.get("role") or "")
        role_type = infer_role_type(role, is_reviewer=bool(row.get("is_reviewer")),
                                    declared=str(row.get("role_type") or ""))
        cid = contract_id_for(row, role_type)
        if cid in contracts:
            out.setdefault(cid, []).append(role)
    return out


def record_session(data_root: Path, project_id: str, run_id: str,
                   team: Sequence[Mapping[str, Any]],  # Any: agent rows
                   ledger: Mapping[str, Mapping[str, Any]] | None = None,  # Any: JSON
                   ) -> list[dict[str, Any]]:  # Any: JSON rows
    """Score each team contract's declared measures for an accepted session and append them."""
    contracts = load_for_project(data_root, project_id)
    values = session_values(Path(data_root) / "runs" / run_id, ledger)
    rows = []
    now = time.time()
    for cid, roles in sorted(_team_contracts(team, contracts).items()):
        keys = [m.key for m in contracts[cid].measures if m.key in values]
        if keys:
            rows.append({"run_id": run_id, "at": now, "contract": cid, "roles": roles,
                         "values": {k: values[k] for k in keys}})
    if rows:
        path = Path(data_root) / "projects" / project_id / MEASURES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    return rows


def history(data_root: Path, project_id: str) -> list[dict[str, Any]]:  # Any: JSON rows
    """Every recorded row, oldest first."""
    return _jsonl(Path(data_root) / "projects" / project_id / MEASURES_FILE)


def _bad(measure: str, value: float) -> bool:
    way, limit = THRESHOLDS.get(measure, ("above", float("inf")))
    return value > limit if way == "above" else value < limit


def propose_rules(data_root: Path, project_id: str) -> list[RuleProposal]:
    """Rules backed by at least `MIN_EVIDENCE` bad sessions out of each contract's last
    `WINDOW`, skipping rules the contract already has."""
    contracts = load_for_project(data_root, project_id)
    by_contract: dict[str, list[dict[str, Any]]] = {}  # Any: JSON rows
    for row in history(data_root, project_id):
        by_contract.setdefault(str(row.get("contract")), []).append(row)
    out: list[RuleProposal] = []
    for cid, rows in sorted(by_contract.items()):
        contract = contracts.get(cid)
        if contract is None:
            continue
        recent = rows[-WINDOW:]
        for measure, rule in RULES.items():
            if rule in contract.rules:
                continue
            bad = [(str(r["run_id"]), float(r["values"][measure])) for r in recent
                   if measure in (r.get("values") or {})
                   and _bad(measure, float(r["values"][measure]))]
            if len(bad) >= MIN_EVIDENCE:
                out.append(RuleProposal(contract=cid, measure=measure, rule=rule,
                                        evidence_runs=tuple(r for r, _ in bad),
                                        values=tuple(v for _, v in bad)))
    return out


PROPOSED_FILE = "contract_proposals.json"


def propose_into_draft(data_root: Path, project_id: str) -> list[RuleProposal]:
    """Put new rule proposals into the plan draft as undecided `add_contract_rule` changes.
    A (contract, rule) is proposed once; a person may reject it and it does not come back."""
    from core.json_store import read_json, write_json
    from core.plan_drafts import PlanDrafts
    seen_path = Path(data_root) / "projects" / project_id / PROPOSED_FILE
    seen = set(read_json(seen_path, []) or [])
    fresh = [p for p in propose_rules(data_root, project_id)
             if f"{p.contract}|{p.rule}" not in seen]
    if not fresh:
        return []
    drafts = PlanDrafts(data_root, project_id)
    for p in fresh:
        drafts.add_team_change({
            "op": "add_contract_rule", "contract": p.contract, "rule": p.rule,
            "evidence_runs": list(p.evidence_runs), "reason": p.reason(),
            "group": f"Contract rule for {p.contract}"[:40]}, by="retrospective")
        seen.add(f"{p.contract}|{p.rule}")
    write_json(seen_path, sorted(seen))
    return fresh


def add_project_rule(data_root: Path, project_id: str, contract: str, rule: str) -> bool:
    """Append `rule` to the project's override of `contract` (created if missing). False when
    the rule is already there."""
    path = Path(data_root) / "projects" / project_id / "contracts" / f"{contract}.yaml"
    data: dict[str, Any] = {}  # Any: YAML mapping
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        data = dict(loaded) if isinstance(loaded, Mapping) else {}
    rules = [str(r) for r in data.get("rules") or []]
    if rule in rules:
        return False
    data["rules"] = rules + [rule]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return True


def render_for_pm(data_root: Path, project_id: str) -> str:
    """The project manager's view: each contract's measures over the last sessions."""
    by_contract: dict[str, list[dict[str, Any]]] = {}  # Any: JSON rows
    for row in history(data_root, project_id):
        by_contract.setdefault(str(row.get("contract")), []).append(row)
    lines = []
    for cid, rows in sorted(by_contract.items()):
        recent = rows[-WINDOW:]
        keys = sorted({k for r in recent for k in (r.get("values") or {})})
        parts = [f"{k} " + "/".join(f"{r['values'][k]:g}" for r in recent if k in r["values"])
                 for k in keys]
        lines.append(f"- {cid}: " + "; ".join(parts))
    if not lines:
        return ""
    return ("Contract measures over the last sessions (oldest first; computed by GuudFlow):\n"
            + "\n".join(lines))


__all__ = ["RULES", "THRESHOLDS", "RuleProposal", "add_project_rule", "history",
           "propose_into_draft", "propose_rules", "record_session", "render_for_pm", "session_values"]
