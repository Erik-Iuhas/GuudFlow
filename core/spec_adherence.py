"""What a run's reviews say about the spec (project-flow plan §1 calls 5-7).

A reviewer answers one `ItemVerdict` per line of a card: the card's own acceptance lines
(those a PM-made item copied from a requirement start with `R4.2:`) and one line per binding
rule (`rule-2: ...`). This module reads those verdicts back from a run's board
(`runs/{run_id}/board.jsonl`, live while the run goes, `board.json` when it was only snapshotted)
and turns them into:

  - check states, written into the spec when the session is accepted (`record_acceptance`);
  - per-rule met and unmet counts, kept on the run state as `rule_verdicts`;
  - the adherence summary (`unmet`, `fixed`, `unmet_merged`) behind the Team tab's panel;
  - the reviewer-seat decision `needs_cosign`.

Everything here reads; only `record_acceptance` writes (spec check states, the run row).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.backlog import Backlog, BacklogItem
from core.spec import Spec, set_check_states
from core.work_db import WorkDB

log = logging.getLogger(__name__)

# A verdict's `item` starts with the id of the check or rule it answers.
_LINE_ID = re.compile(r"^\s*(R[0-9]+\.[0-9]+|rule-[A-Za-z0-9]+)\s*:", re.IGNORECASE)
CHECK_LINE = re.compile(r"^\s*(R[0-9]+\.[0-9]+)\s*:", re.IGNORECASE)


def line_id(text: str) -> str | None:
    """The check or rule id a line starts with ("R4.2", "rule-2"), else None."""
    found = _LINE_ID.match(text)
    if not found:
        return None
    ident = found.group(1)
    return ident.upper() if ident[:1] in "Rr" and "." in ident else ident.lower()


def check_id(text: str) -> str | None:
    """The `R#.#` id an acceptance line starts with, else None."""
    found = CHECK_LINE.match(text)
    return found.group(1).upper() if found else None


@dataclass(frozen=True)
class Review:
    """One reviewer result on one card, reduced to the lines that carry an id."""

    task_id: str
    reviewer: str
    verdict: str
    lines: tuple[tuple[str, bool], ...]    # (check or rule id, met), in answer order


def read_board(run_root: Path) -> list[dict[str, Any]]:  # Any: signal rows
    """The run's board as signal rows: `board.jsonl` first, else `board.json`; [] if none."""
    live = Path(run_root) / "board.jsonl"
    rows: list[dict[str, Any]] = []
    if live.is_file():
        for line in live.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
    snap = Path(run_root) / "board.json"
    if snap.is_file():
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    return []


def _detail(row: Mapping[str, Any]) -> dict[str, Any]:  # Any: signal detail JSON
    try:
        value = json.loads(row.get("detail") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def reviews(board: Iterable[Mapping[str, Any]]) -> list[Review]:  # Any: signal rows
    """Every reviewer result on the board, oldest first."""
    out: list[Review] = []
    for row in board:
        if row.get("type") != "AGENT_RESULT":
            continue
        result = _detail(row).get("result")
        if not isinstance(result, dict) or "verdict" not in result:
            continue
        lines: list[tuple[str, bool]] = []
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            ident = line_id(str(item.get("item", "")))
            if ident is not None:
                lines.append((ident, bool(item.get("met"))))
        out.append(Review(task_id=str(result.get("task_id") or ""),
                          reviewer=str(row.get("from_agent") or ""),
                          verdict=str(result.get("verdict")), lines=tuple(lines)))
    return out


def final_columns(board: Iterable[Mapping[str, Any]]) -> dict[str, str]:  # Any: signal rows
    """Where each card ended up (from TASK_MOVED signals)."""
    out: dict[str, str] = {}
    for row in board:
        if row.get("type") == "TASK_MOVED":
            detail = _detail(row)
            if detail.get("task_id") and detail.get("to"):
                out[str(detail["task_id"])] = str(detail["to"])
    return out


def check_verdicts(found: Sequence[Review]) -> dict[str, bool]:
    """The latest verdict per check id (`R4.2`) over every review: later wins."""
    out: dict[str, bool] = {}
    for review in found:
        for ident, met in review.lines:
            if not ident.startswith("rule-"):
                out[ident] = met
    return out


def rule_counts(found: Sequence[Review]) -> dict[str, dict[str, int]]:
    """Per rule id: how many verdicts said met and unmet, over every review round."""
    out: dict[str, dict[str, int]] = {}
    for review in found:
        for ident, met in review.lines:
            if ident.startswith("rule-"):
                out.setdefault(ident, {"met": 0, "unmet": 0})["met" if met else "unmet"] += 1
    return out


def adherence(board: Sequence[Mapping[str, Any]]) -> dict[str, Any]:  # Any: JSON summary
    """`{rule_verdicts, unmet, fixed, unmet_merged}` for one run's board.

    Per (card, rule) the verdicts are read in order: `unmet` counts pairs whose latest
    verdict is unmet, `fixed` pairs that were unmet in an earlier round and met later, and
    `unmet_merged` pairs whose card reached `done` with the rule still unmet (it must be 0).
    """
    found = reviews(board)
    columns = final_columns(board)
    history: dict[tuple[str, str], list[bool]] = {}
    for review in found:
        for ident, met in review.lines:
            if ident.startswith("rule-"):
                history.setdefault((review.task_id, ident), []).append(met)
    unmet = fixed = merged = 0
    for (task_id, _), seq in history.items():
        if not seq[-1]:
            unmet += 1
            if columns.get(task_id) == "done":
                merged += 1
        elif not all(seq):
            fixed += 1
    return {"rule_verdicts": rule_counts(found), "unmet": unmet, "fixed": fixed,
            "unmet_merged": merged}


# ── the verification ledger (role contracts plan §4.4) ──────────────────────
CHECK_LINKS_FILE = "check_links.json"


def store_check_links(data_root: Path, run_id: str, links: Mapping[str, str]) -> None:
    """Keep a run's check → existing test links (adopt), merged with any already stored."""
    path = Path(data_root) / "runs" / run_id / CHECK_LINKS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**run_check_links(data_root, run_id), **dict(links)}
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def run_check_links(data_root: Path, run_id: str) -> dict[str, str]:
    """The run's check → existing test links (empty when it has none)."""
    path = Path(data_root) / "runs" / run_id / CHECK_LINKS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def run_check_ids(data_root: Path, project_id: str, run_id: str) -> list[str]:
    """The spec checks a run served: its items' check references and its cards' `serves`."""
    ids: list[str] = []
    for item in run_items(data_root, project_id, run_id):
        for line in item.acceptance:
            cid = check_id(line) or (line.strip().upper() if _BARE_CHECK.match(line.strip())
                                     else None)
            if cid:
                ids.append(cid)
    tasks = Path(data_root) / "runs" / run_id / "tasks.jsonl"
    if tasks.is_file():
        last: dict[str, dict[str, Any]] = {}  # Any: JSON rows
        for line in tasks.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("task_id"):
                last[str(row["task_id"])] = row
        for row in last.values():
            ids += [str(s).upper() for s in row.get("serves") or [] if "." in str(s)]
            ids += [c for c in (check_id(a) for a in row.get("acceptance_criteria") or []) if c]
    ids += list(run_check_links(data_root, run_id))
    return list(dict.fromkeys(ids))


_BARE_CHECK = re.compile(r"^R[0-9]+\.[0-9]+$", re.IGNORECASE)


def ledger_for_run(data_root: Path, project_id: str, run_id: str,
                   tests: Mapping[str, str] | None,
                   before: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:  # Any: JSON
    """The run's ledger: each served check's state and evidence (see core/ledger.py).

    `tests` are the check command's per-test results (None: no per-test report), `before` the
    same tests at the run's base (test first)."""
    from core.ledger import CheckInfo, build_ledger, outcomes_from

    spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
    known = {c.id: c for r in spec.requirements for c in r.checks}
    links = run_check_links(data_root, run_id)
    infos = [CheckInfo(id=cid, method=known[cid].method or ("unit" if cid in links else None),
                       test=known[cid].test or links.get(cid))
             for cid in run_check_ids(data_root, project_id, run_id) if cid in known]
    verdicts = check_verdicts(reviews(read_board(Path(data_root) / "runs" / run_id)))
    ledger = build_ledger(infos, outcomes_from(tests) if tests is not None else None, verdicts,
                          before=outcomes_from(before) if before is not None else None)
    return {cid: entry.to_dict() for cid, entry in ledger.items()}


# ── accept ──────────────────────────────────────────────────────────────────
def record_acceptance(data_root: Path, project_id: str, run_id: str,
                      confirmed: Sequence[str] = (), by: str = "owner") -> None:
    """A run was accepted: write its final check states into the spec, and keep its rule
    verdicts on the run state. Never raises: the merge has already happened.

    With a ledger on the run (the check command reported per test), each check's state and
    evidence come from the ledger, so a failing test outweighs a reviewer's "met"; `confirmed`
    are the manual or visual checks the owner ticked. Without one, the reviewers' verdicts are
    used as before (evidence "claimed")."""
    from core.ledger import confirm, spec_states
    from core.project_repo import ProjectRepo, ProjectRepoError

    run_root = Path(data_root) / "runs" / run_id
    try:
        board = read_board(run_root)
        if not board:
            return
        found = reviews(board)
        repo = ProjectRepo(data_root, project_id)
        run = repo.get(run_id)
        stored = (run.check or {}).get("ledger") if run is not None and run.check else None
        ledger = stored if isinstance(stored, dict) else ledger_for_run(
            data_root, project_id, run_id, None)
        if confirmed:
            ledger = confirm(ledger, confirmed, by)
        decided = spec_states(ledger)
        verdicts = check_verdicts(found)
        states = {**{k: v for k, v in verdicts.items() if k not in decided},
                  **{k: met for k, (met, _) in decided.items()}}
        evidence = {**{k: "claimed" if v else "unmet" for k, v in verdicts.items()},
                    **{k: proof for k, (_, proof) in decided.items()}}
        links = run_check_links(data_root, run_id)
        proven = {cid: links[cid] for cid, entry in ledger.items()
                  if cid in links and isinstance(entry, dict) and entry.get("state") == "verified"}
        if states or proven:
            with WorkDB(Path(data_root) / "projects" / project_id).spec_doc() as doc:
                if states:
                    set_check_states(doc, states, run_id=run_id, at=time.time(),
                                     evidence=evidence)
                # An existing test the session proved becomes the check's linked test, so later
                # sessions keep checking it (accepting is the person's approval).
                for req in doc.get("requirements", []):
                    for check in req.get("checks", []):
                        if check.get("id") in proven and not check.get("test"):
                            check["test"] = proven[check["id"]]
                            check["method"] = check.get("method") or "unit"
        if run is not None and run.check is not None:
            try:
                repo.record(run_id, check={**run.check, "ledger": ledger})
            except ProjectRepoError:
                log.warning("run %s has no state row to keep its ledger on", run_id)
        counts = rule_counts(found)
        try:
            ProjectRepo(data_root, project_id).record(run_id, rule_verdicts=counts)
        except ProjectRepoError:
            log.warning("run %s has no state row to keep its rule verdicts on", run_id)
    except Exception:  # noqa: BLE001 — see docstring
        log.exception("recording the spec verdicts of run %s failed", run_id)


# ── reviewer seat ───────────────────────────────────────────────────────────
def run_items(data_root: Path, project_id: str, run_id: str) -> list[BacklogItem]:
    """The backlog items a run holds, or took (its selection snapshot) once it is decided."""
    held = Backlog(data_root, project_id).in_run(run_id)
    if held:
        return held
    from core import project_runs
    return project_runs.selection(Path(data_root) / "runs" / run_id)


def needs_cosign(data_root: Path, project_id: str, run_id: str,
                 reviewer_seat: str) -> bool:
    """Does accepting this run need a human co-sign?

    `all`: always. `must`: only when one of the run's items serves a `must` requirement.
    """
    if reviewer_seat == "all":
        return True
    if reviewer_seat != "must":
        return False
    items = run_items(data_root, project_id, run_id)
    if not items:
        return False
    spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
    musts = {r.id for r in spec.requirements if r.status == "active" and r.priority == "must"}
    return any(s in musts for item in items for s in item.serves)


def cosigned(cosign: Mapping[str, Any] | None) -> bool:  # Any: stored JSON
    """Is there a co-sign verdict on the run?"""
    return bool(cosign) and cosign.get("verdict") == "cosign"


# ── after a session ─────────────────────────────────────────────────────────
def session_report(data_root: Path, project_id: str, run_id: str) -> str:
    """A plain-text report of a finished run for the project manager: summary, review notes,
    leftovers and unmet checks. (The manager wraps it as untrusted data.)"""
    from core.project_repo import ProjectRepo

    root = Path(data_root) / "runs" / run_id
    board = read_board(root)
    run = ProjectRepo(data_root, project_id).get(run_id)
    lines: list[str] = []
    if run is not None:
        lines.append(f"Summary: run {run_id} is {run.state}: {run.commits} commit(s), "
                     f"{len(run.files)} file(s) changed.")
    notes: list[str] = []
    if run is not None and run.note:
        notes.append(f"Decision note: {run.note}")
    cosign = run.cosign if run is not None else None
    if isinstance(cosign, dict):
        for c in cosign.get("comments") or []:
            if isinstance(c, dict):
                notes.append(f"Reviewer ({c.get('severity', 'note')}): {c.get('text', '')}")
    for row in board:
        if row.get("type") != "AGENT_RESULT":
            continue
        result = _detail(row).get("result")
        if not isinstance(result, dict):
            continue
        if "verdict" not in result:
            if result.get("task_id") and result.get("summary"):
                lines.append(f"- {result['task_id']}: {str(result['summary'])[:300]}")
        elif result.get("verdict") == "fail":
            task = result.get("task_id")
            for it in result.get("items") or []:
                if isinstance(it, dict) and not it.get("met"):
                    notes.append(f"Review of {task}: {it.get('item')}: {it.get('detail')}")
            for issue in result.get("issues") or []:
                if isinstance(issue, dict):
                    notes.append(f"Review of {task}: {issue.get('file')}: {issue.get('detail')}")
    lines += ["", "Review notes:"] + (notes or ["(none)"])
    left: list[str] = []
    for card in (_read_json(root / "carry_forward.json") or {}).get("open_cards") or []:
        if isinstance(card, dict):
            left.append(f"- {card.get('task_id')} {card.get('title')} "
                        f"(ended in {card.get('column')})")
    left += [f"- {i.id} {i.title}" for i in Backlog(data_root, project_id).items()
             if i.source == f"run:{run_id}"]
    lines += ["", "Leftovers:"] + (left or ["(none)"])
    spec = Spec(WorkDB(Path(data_root) / "projects" / project_id).read_spec_doc())
    unmet = [f"- {ident}: {_check_text(spec, ident)}"
             for ident, met in check_verdicts(reviews(board)).items() if not met]
    lines += ["", "Unmet checks:"] + (unmet or ["(none)"])
    return "\n".join(lines)


def _check_text(spec: Spec, ident: str) -> str:
    try:
        return spec.check(ident).text
    except Exception:  # noqa: BLE001 — a check the spec no longer has is still reported
        return "(no longer in the spec)"


def _read_json(path: Path) -> dict[str, Any] | None:  # Any: JSON document
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None
