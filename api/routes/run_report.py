"""Read-only run report and per-project overseer metrics (plan §11.4, §12.3).

`GET /api/runs/{id}/report` gathers what a finished (or running) opencode run left on disk
into one payload for Studio's report page: meta, the retrospective (null until the run
ends), gateway cost per role from `llm_costs.jsonl`, the latest CAPABILITIES per role,
the WEB_REQUEST log, overseer interventions with their outcomes, the carry-forward text and
the merged commits per `agent/{role}` branch. Nothing here writes. Run ids resolve inside
`<data_root>/runs` with `safe_resolve`, and git is only ever run with fixed arguments in the
run's own repo (branch names are matched against a strict pattern before use).

`GET /api/projects/{id}/runs-metrics` returns the playbook's per-run metrics history (a
missing playbook is empty, not an error). The playbook entries come from projects.py.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from api.auth import require_bearer

from api.routes.runs import _run_working_dir
from core.harness.artifacts import safe_resolve
from core.harness.retrospective import CARRY_FORWARD_JSON, RETRO_JSON
from core.playbook import Playbook

router = APIRouter(tags=["report"])

MAX_COMMITS_PER_BRANCH = 100
_BRANCH_RE = re.compile(r"^agent/[A-Za-z0-9][A-Za-z0-9._-]*$")
_SEP = "\x1f"


def _jsonl(path: Path) -> list[dict[str, Any]]:  # Any: free-form JSON rows
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _json_file(path: Path) -> dict[str, Any] | None:  # Any: JSON document
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _detail(row: dict[str, Any]) -> dict[str, Any]:  # Any: signal detail JSON
    try:
        value = json.loads(row.get("detail") or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float:  # Any: untrusted JSON scalar
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cost_rows(run_root: Path) -> dict[str, Any]:  # Any: JSON document
    """Per-role gateway usage from `llm_costs.jsonl`, plus the total."""
    by_role: dict[str, dict[str, Any]] = {}
    for row in _jsonl(run_root / "llm_costs.jsonl"):
        role = str(row.get("role") or "?")
        agg = by_role.setdefault(role, {"role": role, "calls": 0, "input_tokens": 0,
                                        "output_tokens": 0, "cache_read_tokens": 0,
                                        "images": 0, "cost_usd": 0.0})
        agg["calls"] += 1
        agg["input_tokens"] += int(_num(row.get("input_tokens")))
        agg["output_tokens"] += int(_num(row.get("output_tokens")))
        agg["cache_read_tokens"] += int(_num(row.get("cache_read_tokens")))
        agg["images"] += int(_num(row.get("images")))
        agg["cost_usd"] += _num(row.get("cost_usd", row.get("cost")))
    rows = sorted(by_role.values(), key=lambda r: -r["cost_usd"])
    total = {k: sum(r[k] for r in rows) for k in
             ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "images", "cost_usd")}
    total["cost_usd"] = round(total["cost_usd"], 6)
    for r in rows:
        r["cost_usd"] = round(r["cost_usd"], 6)
    return {"rows": rows, "total": total}


def _interventions(board: list[dict[str, Any]],
                   retro: dict[str, Any] | None) -> list[dict[str, Any]]:
    outcomes = list((retro or {}).get("interventions") or [])
    out: list[dict[str, Any]] = []
    for row in (r for r in board if r.get("type") == "OVERSEER_INTERVENTION"):
        detail = _detail(row)
        idx = len(out)
        outcome = outcomes[idx] if idx < len(outcomes) and isinstance(outcomes[idx], dict) else None
        fallback = detail.get("task_id") or detail.get("role") or detail.get("author_role") or ""
        out.append({
            "signal_id": row.get("id"), "timestamp": row.get("timestamp"),
            "type": detail.get("type"),
            "target": str(outcome.get("target") or "") if outcome else str(fallback),
            "rationale": detail.get("rationale", ""),
            "needs_human": bool(detail.get("needs_human")),
            "detail": detail,
            "worked": bool(outcome.get("worked")) if outcome else None,
        })
    return out


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo,
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def branch_commits(repo: Path) -> list[dict[str, Any]]:  # Any: JSON rows
    """Commits made on each `agent/{role}` branch (merged into main by the runner)."""
    if not (repo / ".git").exists():
        return []
    out: list[dict[str, Any]] = []
    refs = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/agent/").split()
    for ref in sorted(r for r in refs if _BRANCH_RE.match(r)):
        raw = _git(repo, "log", "--first-parent", "--no-merges",
                   f"--max-count={MAX_COMMITS_PER_BRANCH}",
                   f"--format=%H{_SEP}%h{_SEP}%s{_SEP}%an{_SEP}%ct", ref, "--")
        commits = []
        for line in raw.splitlines():
            parts = line.split(_SEP)
            if len(parts) == 5:
                commits.append({"sha": parts[0], "short_sha": parts[1], "subject": parts[2],
                                "author": parts[3], "committed_at": int(_num(parts[4]))})
        out.append({"role": ref[len("agent/"):], "branch": ref, "commits": commits})
    return out


def _run_root(request: Request, run_id: str) -> Path:
    runs_root = Path(request.app.state.data_root) / "runs"
    root = safe_resolve(runs_root, run_id)
    if root is None or len(Path(run_id).parts) != 1 or not root.is_dir():
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return root


@router.get("/api/runs/{run_id}/report", dependencies=[Depends(require_bearer)])
async def get_run_report(request: Request, run_id: str) -> dict[str, Any]:  # Any: JSON document
    """Everything the run report page shows, in one read-only payload."""
    root = _run_root(request, run_id)
    store = request.app.state.run_store
    meta = await store.get(run_id) or {"run_id": run_id}
    board = await store.get_board(run_id) or []
    retro = _json_file(root / RETRO_JSON)
    carry = _json_file(root / CARRY_FORWARD_JSON)

    capabilities: dict[str, dict[str, Any]] = {}
    web: list[dict[str, Any]] = []
    for row in board:
        kind = row.get("type")
        if kind == "CAPABILITIES" and row.get("from_agent"):
            capabilities[str(row["from_agent"])] = {
                "role": row["from_agent"], "timestamp": row.get("timestamp"), **_detail(row)}
        elif kind == "WEB_REQUEST":
            web.append({"role": row.get("from_agent"), "timestamp": row.get("timestamp"),
                        **_detail(row)})

    repo = _run_working_dir(Path(request.app.state.data_root), run_id)
    return {
        "run_id": run_id,
        "meta": meta,
        "signal_count": len(board),
        "first_signal_at": board[0].get("timestamp") if board else None,
        "last_signal_at": board[-1].get("timestamp") if board else None,
        "retrospective": retro,
        "carry_forward": carry.get("text") if carry else None,
        "costs": cost_rows(root),
        "capabilities": sorted(capabilities.values(), key=lambda c: str(c["role"])),
        "web": web,
        "interventions": _interventions(board, retro),
        "branches": branch_commits(repo),
    }


def _playbook(request: Request, project_id: str) -> Playbook:
    try:
        return Playbook.load(Path(request.app.state.data_root), project_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="project not found")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"playbook unreadable: {exc}")


@router.get("/api/projects/{project_id}/runs-metrics", dependencies=[Depends(require_bearer)])
async def get_runs_metrics(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The playbook's per-run metrics history, oldest first (the overseer's trend line)."""
    return {"project_id": project_id, "runs": _playbook(request, project_id).metrics_history()}
