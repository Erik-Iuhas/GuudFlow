"""GET /api/dashboard — aggregated run + persona overview for the Dashboard view."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from api.auth import require_bearer

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_COMPLETION_STATUSES = frozenset({"complete", "failed", "max_iterations", "stuck"})
_APPROVAL_ID_RE = re.compile(r"approval_id=([^;]+)")


def _unresolved_approvals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Port of the TS `unresolvedApprovals` in Run.tsx."""
    resolved: set[str] = set()
    for s in signals:
        if s.get("type") in ("APPROVAL_GRANTED", "APPROVAL_DENIED"):
            m = _APPROVAL_ID_RE.search(s.get("detail") or "")
            if m:
                resolved.add(m.group(1).strip())
    return [
        s for s in signals
        if s.get("type") == "APPROVAL_NEEDED"
        and (m := _APPROVAL_ID_RE.search(s.get("detail") or ""))
        and m.group(1).strip() not in resolved
    ]


def _elapsed_s(started_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return max(0, int((datetime.now(tz=timezone.utc) - started).total_seconds()))
    except (ValueError, TypeError):
        return 0


def _task_progress(workspace) -> dict[str, int]:
    """Compute {closed, total} from workspace.tasks."""
    tasks = getattr(workspace, "tasks", {})
    total = sum(1 for t in tasks.values() if getattr(t, "status", "") != "CANCELLED")
    closed = sum(1 for t in tasks.values() if getattr(t, "status", "") == "COMPLETE")
    return {"closed": closed, "total": total}


def _persona_recent_activity(rep: dict[str, Any]) -> int:
    """Extract a comparable activity count from reputation dict."""
    prod = rep.get("production", {})
    return prod.get("recent_activity_count") or prod.get("total_runs", 0)


@router.get("")
async def get_dashboard(
    request: Request,
    _: None = Depends(require_bearer),
) -> dict:
    run_registry = getattr(request.app.state, "run_registry", None)
    run_store = getattr(request.app.state, "run_store", None)
    persona_store = getattr(request.app.state, "persona_store", None)

    # ── Active runs ──────────────────────────────────────────────────────
    active_runs: list[dict[str, Any]] = []
    active_run_ids: set[str] = set()

    if run_registry is not None:
        for run_id, workspace in list(run_registry.runs.items()):
            meta: dict[str, Any] = {}
            if run_store is not None:
                meta = (await run_store.get(run_id)) or {}

            # Collect current agents from workspace.agent_states
            current_agents = []
            agent_states: dict[str, Any] = getattr(workspace, "agent_states", {})
            hired_personas: dict[str, str] = getattr(workspace, "hired_personas", {})
            for role, state in agent_states.items():
                current_agents.append({
                    "role": role,
                    "state": state.value if hasattr(state, "value") else str(state),
                    "persona_id": hired_personas.get(role),
                })

            active_runs.append({
                "run_id": run_id,
                "project_id": meta.get("project_id", ""),
                "project_name": meta.get("project_name", ""),
                "goal": meta.get("goal", ""),
                "status": "running",
                "current_agents": current_agents,
                "started_at": meta.get("created_at"),
                "elapsed_s": _elapsed_s(meta.get("created_at")),
                "task_progress": _task_progress(workspace),
            })
            active_run_ids.add(run_id)

    # ── Recent completions ───────────────────────────────────────────────
    recent_completions: list[dict[str, Any]] = []
    if run_store is not None:
        all_runs = await run_store.list(limit=20)
        for r in all_runs:
            if r.get("run_id") in active_run_ids:
                continue
            if r.get("status") not in _COMPLETION_STATUSES:
                continue
            recent_completions.append({
                "run_id": r.get("run_id", ""),
                "project_id": r.get("project_id", ""),
                "outcome": r.get("status", ""),
                "duration_s": r.get("duration_s"),
                "ended_at": r.get("ended_at") or r.get("updated_at"),
            })
            if len(recent_completions) >= 10:
                break

    # ── Pending approvals (across active runs) ───────────────────────────
    pending_approvals: list[dict[str, Any]] = []
    if run_registry is not None and run_store is not None:
        for run_id, workspace in list(run_registry.runs.items()):
            # Use workspace.pending_approvals directly (authoritative)
            for req in getattr(workspace, "pending_approvals", []):
                pending_approvals.append({
                    "run_id": run_id,
                    "approval_id": req.id,
                    "title": req.summary,
                    "kind": req.kind,
                })
                if len(pending_approvals) >= 20:
                    break
            if len(pending_approvals) >= 20:
                break

    # ── Top personas ─────────────────────────────────────────────────────
    top_personas: list[dict[str, Any]] = []
    if persona_store is not None:
        try:
            personas = await persona_store.list()
        except Exception:
            personas = []
        personas.sort(
            key=lambda p: _persona_recent_activity(p.get("reputation") or {}),
            reverse=True,
        )
        for p in personas[:8]:
            rep = p.get("reputation") or {}
            prod = rep.get("production") or {}
            total = prod.get("total_runs", 0)
            wins = prod.get("approved_runs", 0)
            top_personas.append({
                "persona_id": p.get("persona_id", ""),
                "name": p.get("name", ""),
                "role": p.get("specialty", ""),
                "recent_runs": total,
                "win_rate": round(wins / total, 2) if total else 0.0,
            })

    return {
        "active_runs": active_runs,
        "recent_completions": recent_completions,
        "pending_approvals": pending_approvals,
        "top_personas": top_personas,
    }
