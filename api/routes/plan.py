"""/api/projects/{id}/spec and /plan: the plan conversation and the versioned spec (plan §6).

The project manager (`core/project_manager.py`) proposes changes into one open draft; a person
ticks them and approves, which applies them to the spec and the backlog in one transaction.
All routes need the bearer token.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from api.auth import require_bearer
from api.routes.pm import remember_draft
from core.backlog import BacklogError
from core.plan_drafts import TEAM_OPS, Draft, PlanDraftError, PlanDrafts, parse_change
from core.project_manager import ProjectManager, ProjectManagerError, TurnOutcome
from core.spec import Spec, SpecError
from core.team_changes import TeamChangeError, apply_team_changes

router = APIRouter(prefix="/api/projects", tags=["plan"],
                   dependencies=[Depends(require_bearer)])

APPROVER = "human"


class MessageBody(BaseModel):
    """Body of POST /plan/messages."""

    text: str
    mode: Literal["update", "discuss"] = "update"
    meta_model: str | None = None


class StartBody(BaseModel):
    """Body of POST /plan/start."""

    meta_model: str | None = None


class TickBody(BaseModel):
    """Body of PATCH /plan/draft/{cid}: true ticks, false rejects, null resets."""

    accepted: bool | None


class EditBody(BaseModel):
    """Body of POST /spec/edit: one change, as in a draft (`{"op": "edit_requirement", ...}`)."""

    change: dict[str, Any]  # Any: a `Change` union member, validated by parse_change


async def _project(request: Request, project_id: str) -> tuple[Path, dict[str, Any]]:
    store = request.app.state.project_store
    doc = await store.get(project_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return Path(request.app.state.data_root), doc


def _draft_json(draft: Draft | None) -> dict[str, Any] | None:  # Any: JSON
    return None if draft is None else draft.to_dict()


def _outcome_json(outcome: TurnOutcome) -> dict[str, Any]:  # Any: JSON
    return {"messages": outcome.messages, "draft": _draft_json(outcome.draft)}


def _draft_error(exc: Exception) -> HTTPException:
    text = str(exc)
    code = 404 if ("unknown draft" in text or "has no change" in text) else 409
    return HTTPException(status_code=code, detail=text)


def _pm_model(request: Request, data_root: Path, tier: str) -> str | None:
    """The model id the tier resolves to (None when a test injected the LLM)."""
    if getattr(request.app.state, "meta_llm", None) is not None:
        return None
    from core.llm.registry import ModelRegistry
    try:
        registry = (getattr(request.app.state, "model_registry", None)
                    or ModelRegistry.for_data_root(data_root))
        return registry.resolve(tier).id
    except Exception:  # noqa: BLE001 — fall back to the client's default model
        return None


async def _manager(request: Request, project_id: str,
                   meta_model: str | None) -> ProjectManager:
    from api.routes.meta import _build_meta_llm

    data_root, doc = await _project(request, project_id)
    tier = meta_model or "heavy"
    llm = _build_meta_llm(request, meta_model=tier)
    return ProjectManager(llm, data_root, project_id, doc, model=_pm_model(request, data_root, tier))


async def _run_turn(request: Request, project_id: str, meta_model: str | None,
                    text: str | None, mode: Literal["update", "discuss"]) -> dict[str, Any]:  # Any: JSON
    pm = await _manager(request, project_id, meta_model)
    try:
        outcome = await (pm.start() if text is None else pm.turn(text, mode=mode))
    except ProjectManagerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except PlanDraftError as exc:
        raise _draft_error(exc) from None
    except Exception as exc:  # noqa: BLE001 — an LLM failure is a 502, not a crash
        raise HTTPException(status_code=502,
                            detail=f"the project manager could not answer: {exc}") from None
    return _outcome_json(outcome)


# ── spec ────────────────────────────────────────────────────────────────────
@router.get("/{project_id}/spec")
async def get_spec(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The approved spec: `{version, requirements, decisions, rules}`."""
    data_root, _ = await _project(request, project_id)
    spec = Spec(await asyncio.to_thread(PlanDrafts(data_root, project_id).db.read_spec_doc))
    return {"version": spec.version,
            "requirements": [r.to_dict() for r in spec.requirements],
            "decisions": [d.to_dict() for d in spec.decisions],
            "rules": [r.to_dict() for r in spec.rules]}


@router.post("/{project_id}/spec/edit")
async def edit_spec(request: Request, project_id: str, body: EditBody) -> dict[str, Any]:  # Any: JSON
    """A human's direct edit: a one-change draft, approved on the spot."""
    data_root, _ = await _project(request, project_id)
    try:
        change = parse_change(body.change)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_pydantic_detail(exc)) from None
    if change.op in TEAM_OPS:
        raise HTTPException(status_code=422, detail="team changes go through a draft; "
                            "edit the team on the Team tab")
    try:
        result = await asyncio.to_thread(PlanDrafts(data_root, project_id).apply_direct,
                                         change, APPROVER)
    except (PlanDraftError, SpecError, BacklogError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return asdict(result)


def _pydantic_detail(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


# ── plan conversation ───────────────────────────────────────────────────────
@router.get("/{project_id}/plan")
async def get_plan(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """The conversation and the open draft: `{messages, draft}`."""
    data_root, _ = await _project(request, project_id)
    drafts = PlanDrafts(data_root, project_id)
    messages = await asyncio.to_thread(drafts.messages, 500)
    return {"messages": messages, "draft": _draft_json(await asyncio.to_thread(drafts.current))}


@router.post("/{project_id}/plan/start")
async def start_plan(request: Request, project_id: str,
                     body: StartBody | None = None) -> dict[str, Any]:  # Any: JSON
    """Ask the project manager for the first draft. 409 when the conversation has begun."""
    data_root, _ = await _project(request, project_id)
    if await asyncio.to_thread(PlanDrafts(data_root, project_id).messages, 1):
        raise HTTPException(status_code=409, detail="planning has already started")
    return await _run_turn(request, project_id, (body or StartBody()).meta_model, None, "update")


@router.post("/{project_id}/plan/messages")
async def post_message(request: Request, project_id: str,
                       body: MessageBody) -> dict[str, Any]:  # Any: JSON
    """A human message. `update` puts the manager's changes into the draft; `discuss` does not.

    Returns the new messages (the human's, then the manager's) and the open draft.
    """
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="text is empty")
    return await _run_turn(request, project_id, body.meta_model, body.text, body.mode)


@router.patch("/{project_id}/plan/draft/{cid}")
async def tick_change(request: Request, project_id: str, cid: str,
                      body: TickBody) -> dict[str, Any]:  # Any: JSON
    """Tick (true), reject (false) or reset (null) one change; returns the draft."""
    data_root, _ = await _project(request, project_id)
    drafts = PlanDrafts(data_root, project_id)
    current = await asyncio.to_thread(drafts.current)
    if current is None:
        raise HTTPException(status_code=409, detail="there is no open draft")
    try:
        draft = await asyncio.to_thread(drafts.set_change, current.id, cid, body.accepted)
    except PlanDraftError as exc:
        raise _draft_error(exc) from None
    return draft.to_dict()


@router.post("/{project_id}/plan/draft/approve")
async def approve_draft(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Apply the ticked changes: `{version, applied, created, team_changes, team}`.

    Ticked team ops (add_role, edit_role, remove_role) then change the project's `agents`;
    `team` is `{added, edited, removed}` (role names). The new team applies from the next
    session.
    """
    data_root, doc = await _project(request, project_id)
    drafts = PlanDrafts(data_root, project_id)
    current = await asyncio.to_thread(drafts.current)
    if current is None:
        raise HTTPException(status_code=409, detail="there is no open draft")
    try:
        result = await asyncio.to_thread(drafts.approve, current.id, APPROVER)
    except PlanDraftError as exc:
        raise _draft_error(exc) from None
    except (SpecError, BacklogError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    await remember_draft(request, project_id, current, True)
    team: dict[str, list[str]] = {"added": [], "edited": [], "removed": []}
    if result.team_changes:
        # QUESTION: known two-store limitation. work.db (spec, drafts) and the project store
        # (projects.json) cannot share a transaction, so a failure here leaves the draft
        # approved with the team unchanged. A retry is not possible (the draft is closed);
        # the person re-proposes the team change.
        try:
            agents, team = apply_team_changes(list(doc.get("agents") or []),
                                              result.team_changes)
            updated = await request.app.state.project_store.update(
                project_id, {**doc, "agents": agents})
            if updated is None:
                raise TeamChangeError(f"project {project_id!r} not found")
        except Exception as exc:  # noqa: BLE001 — reported in full, the draft stays approved
            raise HTTPException(
                status_code=500,
                detail=(f"draft {current.id} was approved (spec version {result.version}) but "
                        f"the team change could not be applied: {exc}")) from None
    return {**asdict(result), "team": team}


@router.post("/{project_id}/plan/draft/discard")
async def discard_draft(request: Request, project_id: str) -> dict[str, Any]:  # Any: JSON
    """Close the open draft without applying anything."""
    data_root, _ = await _project(request, project_id)
    drafts = PlanDrafts(data_root, project_id)
    current = await asyncio.to_thread(drafts.current)
    if current is None:
        raise HTTPException(status_code=409, detail="there is no open draft")
    try:
        await asyncio.to_thread(drafts.discard, current.id)
    except PlanDraftError as exc:
        raise _draft_error(exc) from None
    await remember_draft(request, project_id, current, False)
    return {}


# ── the spec in sessions (plan §1 calls 5-7, §6) ────────────────────────────
@router.post("/{project_id}/plan/after-session/{run_id}")
async def after_session(request: Request, project_id: str, run_id: str,
                        body: StartBody | None = None) -> dict[str, Any]:  # Any: JSON
    """Ask the project manager what a finished session means for the spec.

    The report (summary, review notes, leftovers, unmet checks) is built from the run. In
    `prototype_first` and `research_first` projects the first accepted session derives the spec
    instead. Returns `{messages, draft}`; 502 when the model fails.
    """
    from core.project_repo import ProjectRepo
    from core.spec_adherence import session_report

    data_root, doc = await _project(request, project_id)
    run = await asyncio.to_thread(ProjectRepo(data_root, project_id).get, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found in this project")
    report = await asyncio.to_thread(session_report, data_root, project_id, run_id)
    accepted = sorted((r for r in ProjectRepo(data_root, project_id).runs()
                       if r.state == "accepted"), key=lambda r: r.decided_at or 0.0)
    first = bool(accepted) and accepted[0].run_id == run_id
    pm = await _manager(request, project_id, (body or StartBody()).meta_model)
    derive = first and pm.start_mode in ("prototype_first", "research_first")
    try:
        outcome = await (pm.derive_spec(run_id, report) if derive
                         else pm.after_session(run_id, report))
    except ProjectManagerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except PlanDraftError as exc:
        raise _draft_error(exc) from None
    except Exception as exc:  # noqa: BLE001 — an LLM failure is a 502, not a crash
        raise HTTPException(status_code=502,
                            detail=f"the project manager could not answer: {exc}") from None
    return _outcome_json(outcome)


@router.get("/{project_id}/adherence")
async def get_adherence(request: Request, project_id: str,
                        last: int = Query(3, ge=1, le=50)) -> dict[str, Any]:  # Any: JSON
    """How the last `last` sessions kept the binding rules, from their reviews.

    `{runs: [{run_id, rule_verdicts, unmet, fixed}], unmet_merged}`; `unmet_merged` counts
    rules still unmet on cards that finished `done` and must be 0.
    """
    from core.project_repo import ProjectRepo
    from core.spec_adherence import adherence, read_board

    data_root, _ = await _project(request, project_id)

    def compute() -> dict[str, Any]:  # Any: JSON
        rows: list[dict[str, Any]] = []  # Any: JSON
        merged = 0
        for run in ProjectRepo(data_root, project_id).runs()[:last]:
            board = read_board(data_root / "runs" / run.run_id)
            result = adherence(board)
            merged += result["unmet_merged"]
            rows.append({"run_id": run.run_id,
                         "rule_verdicts": result["rule_verdicts"] or run.rule_verdicts or {},
                         "unmet": result["unmet"], "fixed": result["fixed"]})
        return {"runs": rows, "unmet_merged": merged}

    return await asyncio.to_thread(compute)


runs_router = APIRouter(prefix="/api/runs", tags=["plan"],
                        dependencies=[Depends(require_bearer)])


@runs_router.get("/{run_id}/spec-slice")
async def get_spec_slice(request: Request, run_id: str) -> dict[str, Any]:  # Any: JSON
    """What the run's items oblige the team to: requirements, decisions, rules and checks,
    with the verdicts this run's reviews have given so far (works while the run is live)."""
    from core.harness.spec_brief import spec_slice

    meta = await request.app.state.run_store.get(run_id)
    if meta is None or not meta.get("project_id"):
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    project_id = str(meta["project_id"])
    data_root, doc = await _project(request, project_id)
    return await asyncio.to_thread(spec_slice, data_root, project_id, run_id,
                                   doc.get("start_mode"))
