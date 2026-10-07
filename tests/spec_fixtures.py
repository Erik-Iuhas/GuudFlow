"""Shared helpers for the spec-in-sessions tests: a seeded spec and scripted run boards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.spec import Check, Decision, Requirement, Rule
from core.work_db import WorkDB

R1_TEXT = "Users can save a recipe"
RULE1 = "All money amounts are integer cents"
RULE2 = "Every endpoint has a test"
PREF = "Prefer small functions"


def seed_spec(data_root: Path, project_id: str = "p1") -> None:
    """R1 (must, two checks), R2 (should, one check), D1 binding, rule-1 and rule-2 binding
    (rule-2 only for testers), rule-3 a preference."""
    reqs = [
        Requirement(id="R1", text=R1_TEXT, kind="feature", priority="must",
                    checks=[Check("R1.1", "a saved recipe shows in the list"),
                            Check("R1.2", "an empty title is refused")]),
        Requirement(id="R2", text="Recipes can be tagged", kind="feature", priority="should",
                    checks=[Check("R2.1", "a tag filters the list")]),
    ]
    decs = [Decision(id="D1", title="Storage", choice="SQLite",
                     reason="boring and testable, no server to run", serves=["R1"], binding=True),
            Decision(id="D2", title="Naming", choice="snake_case everywhere",
                     reason="matches the existing code base", serves=["R2"])]
    rules = [Rule(id="rule-1", text=RULE1, source="human"),
             Rule(id="rule-2", text=RULE2, source="human", applies_to=["tester"]),
             Rule(id="rule-3", text=PREF, source="human", strength="preference")]
    with WorkDB(Path(data_root) / "projects" / project_id).spec_doc() as doc:
        doc["version"] = 3
        doc["next"] = {"R": 3, "D": 3, "rule": 4}
        doc["requirements"] = [r.to_dict() for r in reqs]
        doc["decisions"] = [d.to_dict() for d in decs]
        doc["rules"] = [r.to_dict() for r in rules]


def signal(kind: str, role: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """A board row as `board.jsonl` stores it."""
    return {"type": kind, "from_agent": role, "summary": kind.lower(),
            "detail": json.dumps(detail) if detail is not None else None}


def review(task_id: str, items: list[tuple[str, bool]], reviewer: str = "reviewer"
           ) -> dict[str, Any]:
    """An AGENT_RESULT row for a reviewer's result (`items`: (line text, met))."""
    verdict = "pass" if all(met for _, met in items) else "fail"
    return signal("AGENT_RESULT", reviewer, {"result": {
        "verdict": verdict, "task_id": task_id, "summary": "checked",
        "items": [{"item": text, "met": met,
                   "detail": "" if met else "the behaviour is missing, see the diff"}
                  for text, met in items], "issues": []}})


def moved(task_id: str, to: str) -> dict[str, Any]:
    """A TASK_MOVED row."""
    return signal("TASK_MOVED", "api", {"task_id": task_id, "to": to})


def write_board(data_root: Path, run_id: str, rows: list[dict[str, Any]],
                project_id: str = "p1") -> Path:
    """`runs/{run_id}/board.jsonl` plus a meta.json so the run store knows the run."""
    root = Path(data_root) / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "board.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                      encoding="utf-8")
    (root / "meta.json").write_text(json.dumps({"run_id": run_id, "project_id": project_id,
                                                "status": "complete"}), encoding="utf-8")
    return root


def with_reasons(payload: dict) -> dict:
    """A PM turn payload whose changes all carry a described reason (plan §12).

    Changes without a `group` get "Test reason"; every group used is described.
    """
    changes = [dict(c) for c in payload.get("changes") or []]
    for c in changes:
        c.setdefault("group", "Test reason")
    described = {g["name"] for g in payload.get("groups") or []}
    groups = list(payload.get("groups") or []) + [
        {"name": n, "summary": f"What changes for {n}.", "because": f"The test asks for {n}."}
        for n in dict.fromkeys(c["group"] for c in changes) if n not in described]
    return {**payload, "changes": changes, "groups": groups}
