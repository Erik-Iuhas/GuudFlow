"""/api/training — training camp endpoints (Phase F.1.2).

Three concerns:

1. `GET /api/training/scenarios` — discover available scenarios
2. `POST /api/training/run` — launch a training run for one persona
3. `POST /api/training/tournament` — launch N runs (one per persona)
   on the same scenario; `GET .../tournaments/{id}` aggregates
   per-run RUBRIC_SCORE signals into a leaderboard.

The tournament endpoint is sequential dispatch in F.1: it builds N
projects, persists them, and enqueues N jobs — the worker drains them
one at a time. Parallel dispatch needs queue/runner changes that are
out of scope for this phase.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from api.auth import require_bearer
from core.training.scenario import Scenario, list_scenarios
from core.training.tournament import (
    TournamentParticipant,
    TournamentRegistry,
    build_training_project,
)
from worker.queue import Job


router = APIRouter(prefix="/api/training", tags=["training"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _scenarios_root(request: Request) -> Path:
    """Where scenarios live. Tests can override
    `app.state.training_scenarios_root`; production falls back to
    `data/training_camp/scenarios/`."""
    override = getattr(request.app.state, "training_scenarios_root", None)
    if override is not None:
        return Path(override)
    return Path(request.app.state.data_root) / "training_camp" / "scenarios"


def _registry(request: Request) -> TournamentRegistry:
    """Lazily attach a TournamentRegistry to app.state. Survives the
    process lifetime; resets on restart."""
    reg = getattr(request.app.state, "tournament_registry", None)
    if reg is None:
        reg = TournamentRegistry()
        request.app.state.tournament_registry = reg
    return reg


def _scenario_to_summary(s: Scenario) -> dict:
    """Public shape — what `GET /scenarios` returns. Hides the on-disk
    Path objects from API consumers."""
    return {
        "scenario_id": s.scenario_id,
        "goal": s.goal,
        "project_type": s.project_type,
        "expected_role_types": list(s.expected_role_types),
        "n_auto_checks": len(s.rubric.auto_checks),
        "total_auto_weight": s.rubric.total_auto_weight,
        "description": s.description,
    }


async def _persona_exists(request: Request, persona_id: str) -> bool:
    store = request.app.state.persona_store
    return (store.root / persona_id).is_dir()


async def _load_scenario_or_404(request: Request, scenario_id: str) -> Scenario:
    root = _scenarios_root(request)
    scenarios = list_scenarios(root)
    for s in scenarios:
        if s.scenario_id == scenario_id:
            return s
    raise HTTPException(status_code=404, detail=f"scenario {scenario_id!r} not found")


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/scenarios")
async def get_scenarios(request: Request) -> list[dict]:
    """Returns every scenario discoverable under the scenarios root,
    sorted alphabetically. Malformed scenarios are silently skipped
    (the loader logs them; one bad scenario shouldn't break listing)."""
    return [_scenario_to_summary(s) for s in list_scenarios(_scenarios_root(request))]


@router.post(
    "/run",
    dependencies=[Depends(require_bearer)],
    status_code=202,
)
async def launch_training_run(
    request: Request,
    body: dict = Body(...),
) -> dict:
    """Launch a single training run.

    Body: { "persona_id": "...", "scenario_id": "..." }
    Returns: { project_id, run_id, job_id }
    """
    persona_id = body.get("persona_id")
    scenario_id = body.get("scenario_id")
    if not persona_id or not scenario_id:
        raise HTTPException(
            status_code=400,
            detail="persona_id and scenario_id are required",
        )
    if not await _persona_exists(request, persona_id):
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")

    scenario = await _load_scenario_or_404(request, scenario_id)

    project_dict = build_training_project(scenario=scenario, persona_id=persona_id)
    await request.app.state.project_store.create(project_dict)

    run_id = f"run_{uuid4().hex[:10]}"
    job_id = f"job_{uuid4().hex[:10]}"
    await request.app.state.queue.enqueue(Job(
        job_id=job_id,
        project_id=project_dict["project_id"],
        run_id=run_id,
        payload={},
    ))

    return {
        "project_id": project_dict["project_id"],
        "run_id": run_id,
        "job_id": job_id,
    }


@router.post(
    "/tournament",
    dependencies=[Depends(require_bearer)],
    status_code=202,
)
async def launch_tournament(
    request: Request,
    body: dict = Body(...),
) -> dict:
    """Launch a tournament — one training run per persona on the same
    scenario. Sequential dispatch through the existing queue.

    Body: { "scenario_id": "...", "persona_ids": ["a", "b", ...] }
    Returns: { tournament_id, scenario_id, participants: [...] }
    """
    scenario_id = body.get("scenario_id")
    persona_ids = body.get("persona_ids") or []
    if not scenario_id:
        raise HTTPException(status_code=400, detail="scenario_id is required")
    if not isinstance(persona_ids, list) or len(persona_ids) < 2:
        raise HTTPException(
            status_code=400,
            detail="persona_ids must be a list with at least 2 personas",
        )
    for pid in persona_ids:
        if not await _persona_exists(request, pid):
            raise HTTPException(
                status_code=404, detail=f"persona {pid!r} not found"
            )

    scenario = await _load_scenario_or_404(request, scenario_id)

    participants: list[TournamentParticipant] = []
    for pid in persona_ids:
        project_dict = build_training_project(scenario=scenario, persona_id=pid)
        await request.app.state.project_store.create(project_dict)
        run_id = f"run_{uuid4().hex[:10]}"
        job_id = f"job_{uuid4().hex[:10]}"
        await request.app.state.queue.enqueue(Job(
            job_id=job_id,
            project_id=project_dict["project_id"],
            run_id=run_id,
            payload={},
        ))
        participants.append(TournamentParticipant(
            persona_id=pid,
            project_id=project_dict["project_id"],
            run_id=run_id,
            job_id=job_id,
        ))

    tournament_id = await _registry(request).register(scenario_id, participants)

    return {
        "tournament_id": tournament_id,
        "scenario_id": scenario_id,
        "participants": [
            {
                "persona_id": p.persona_id,
                "project_id": p.project_id,
                "run_id": p.run_id,
                "job_id": p.job_id,
            }
            for p in participants
        ],
    }


@router.get("/tournaments/{tournament_id}")
async def get_tournament_result(
    request: Request,
    tournament_id: str,
) -> dict:
    """Aggregate the per-run RUBRIC_SCORE signals into a leaderboard.

    Reads each participant's `data/runs/{run_id}/board.json` (written by
    the worker on run completion). Participants whose runs haven't
    finished yet appear with `score: null` so the studio can render
    a "still running" cell.
    """
    entry = await _registry(request).get(tournament_id)
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"tournament {tournament_id!r} not found"
        )

    data_root = Path(request.app.state.data_root)
    leaderboard: list[dict] = []
    for p in entry.participants:
        score = _read_rubric_score(data_root / "runs" / p.run_id)
        leaderboard.append({
            "persona_id": p.persona_id,
            "run_id": p.run_id,
            "project_id": p.project_id,
            "score": score,
        })

    # Sort: known scores descending, then unknown scores last (still
    # running / failed). Ties broken by persona_id for stable display.
    leaderboard.sort(
        key=lambda r: (
            r["score"] is None,
            -(r["score"] or 0.0),
            r["persona_id"],
        ),
    )

    all_scored = all(r["score"] is not None for r in leaderboard)
    return {
        "tournament_id": tournament_id,
        "scenario_id": entry.scenario_id,
        "complete": all_scored,
        "leaderboard": leaderboard,
    }


def _read_rubric_score(run_dir: Path) -> float | None:
    """Pull the RUBRIC_SCORE signal off the run's persisted board.
    Returns None if the run hasn't finished or has no rubric signal."""
    board_path = run_dir / "board.json"
    if not board_path.is_file():
        return None
    try:
        signals = json.loads(board_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    for s in signals:
        if s.get("type") == "RUBRIC_SCORE":
            try:
                detail = json.loads(s.get("detail") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            score = detail.get("score")
            if isinstance(score, (int, float)):
                return float(score)
    return None
