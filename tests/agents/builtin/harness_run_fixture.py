"""Builds small opencode-run directories (board/tasks/costs) for memory tests.

Shapes mirror a real recorded run of tests/harness/test_task_flow.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def sig(type_: str, role: str, summary: str, detail: Any = None) -> dict[str, Any]:
    """One board.jsonl row."""
    return {
        "id": f"{type_}-{role}-{summary[:8]}", "type": type_, "summary": summary,
        "detail": json.dumps(detail) if isinstance(detail, (dict, list)) else detail,
        "from_agent": role, "from_user": False, "priority": "normal",
        "iteration": 1, "seen_by": [], "timestamp": "2026-10-04T18:31:49+00:00",
    }


def agent_result(role: str, result: dict[str, Any], cost: float = 0.0,
                 tools: list[list[str]] | None = None) -> dict[str, Any]:
    """An AGENT_RESULT signal row."""
    return sig("AGENT_RESULT", role, f"{role}: result", {
        "result": result, "cost": cost,
        "tokens": {"input": 30, "output": 15, "reasoning": 0, "cache_read": 0},
        "tool_calls": tools or [["write", "completed"], ["guudflow_submit_result", "completed"]],
        "commits": ["abc"], "files": ["api.py"],
    })


def card(task_id: str, owner: str, column: str, round_: int = 0,
         thread: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One tasks.jsonl snapshot row."""
    return {
        "task_id": task_id, "assigned_to": owner, "title": f"Title {task_id}",
        "goal": f"Title {task_id}", "acceptance_criteria": [f"{task_id} works"],
        "column": column, "review_round": round_, "thread": thread or [],
    }


def review_issue(task_id: str, item: str, text: str) -> dict[str, Any]:
    """A review_issue thread entry."""
    return {"task_id": task_id, "kind": "review_issue", "author": "reviewer",
            "text": text, "iteration": 0, "data": {"item": item}}


def write_run(root: Path, board: list[dict[str, Any]], tasks: list[dict[str, Any]],
              costs: list[dict[str, Any]] | None = None) -> Path:
    """Write the three files under `root` and return it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "board.jsonl").write_text("\n".join(json.dumps(r) for r in board) + "\n")
    (root / "tasks.jsonl").write_text("\n".join(json.dumps(r) for r in tasks) + "\n")
    if costs is not None:
        (root / "llm_costs.jsonl").write_text("\n".join(json.dumps(r) for r in costs) + "\n")
    return root


def cost_row(role: str, cost: float, inp: int = 10, out: int = 5,
             model: str = "fake-model") -> dict[str, Any]:
    """One llm_costs.jsonl row."""
    return {"role": role, "model": model, "input_tokens": inp, "output_tokens": out,
            "cache_read_tokens": 0, "cost_usd": cost}


def reworked_run(root: Path) -> Path:
    """backend: T1 bounced twice on the same item and ended done; T2 clean."""
    item = "T1 works"
    issue = f"{item}: status() returns None, it must return 'ok'"
    board = [
        sig("AGENT_RESULT", "planner", "planner: planned 2 card(s)",
            {"result": {"tasks": []}, "cost": 0.0, "tokens": {}, "tool_calls": [],
             "commits": [], "files": []}),
        agent_result("backend", {"status": "done", "task_id": "T1", "summary": "Added api.py"}, 0.01),
        agent_result("reviewer", {"verdict": "fail", "task_id": "T1", "summary": ""},
                     0.02, [["guudflow_changes", "completed"]]),
        sig("REJECTED", "reviewer", "reviewer sent T1 back to backend (round 1)",
            {"task_id": "T1", "authors": ["backend"], "round": 1}),
        agent_result("backend", {"status": "done", "task_id": "T1", "summary": "Fixed status()"}, 0.01),
        agent_result("reviewer", {"verdict": "fail", "task_id": "T1", "summary": ""}, 0.02),
        sig("REJECTED", "reviewer", "reviewer sent T1 back to backend (round 2)",
            {"task_id": "T1", "authors": ["backend"], "round": 2}),
        agent_result("backend", {"status": "done", "task_id": "T1", "summary": "Really fixed it"}, 0.01),
        agent_result("backend", {"status": "done", "task_id": "T2", "summary": "Page"}, 0.01),
        sig("TOOL_CALL", "backend", "backend: write api.py -> ok",
            {"tool": "write", "status": "completed"}),
    ]
    tasks = [
        card("T1", "backend", "todo"),
        card("T1", "backend", "done", 2, [review_issue("T1", item, issue),
                                          review_issue("T1", item, issue)]),
        card("T2", "backend", "done", 0),
    ]
    costs = [cost_row("planner", 0.0), cost_row("backend", 0.03, 100, 50),
             cost_row("backend", 0.01, 20, 10), cost_row("reviewer", 0.04)]
    return write_run(root, board, tasks, costs)
