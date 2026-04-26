"""/api/personas — read-only persona roster + memory inspection + chat."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from agents.persona import Persona
from agents.persona_chat import chat_with_persona
from api.auth import require_bearer
from core.llm import AnthropicClient, LLMClient


router = APIRouter(prefix="/api/personas", tags=["personas"])


def _store(request: Request):
    return request.app.state.persona_store


def _build_chat_llm(request: Request) -> LLMClient:
    """Tests inject via `app.state.chat_llm`; production falls through to
    the default Anthropic client. Same shape as `_build_meta_llm`."""
    injected = getattr(request.app.state, "chat_llm", None)
    if injected is not None:
        return injected
    return AnthropicClient()


@router.get("")
async def list_personas(request: Request) -> list[dict]:
    return await _store(request).list()


@router.get("/rankings")
async def get_rankings(
    request: Request,
    role_type: str | None = Query(None),
    scenario: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> list[dict]:
    """Leaderboard query (DESIGN §35, Phase F.1.3).

    Ranks personas by average training rubric score, optionally filtered
    by `role_type` (substring match against persona id + specialty) or
    `scenario` (only scores on this scenario id). Routes registered
    BEFORE `/{persona_id}` so the literal path doesn't get captured as
    a persona id.

    Score: simple mean of training-episode rubric scores. The §34
    recency-weighting machinery exists; using it here would bias
    newer runs which is what we want, but the simple mean is fine
    for F.1's surface — recency-aware ranking is a follow-up.
    """
    store = _store(request)
    if not store.root.is_dir():
        return []

    out: list[dict] = []
    for persona_dir in sorted(store.root.iterdir()):
        if not persona_dir.is_dir() or persona_dir.name.startswith("_"):
            continue
        episodes_path = persona_dir / "episodes.jsonl"
        if not episodes_path.is_file():
            continue
        scores: list[float] = []
        for line in episodes_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ep = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not ep.get("training"):
                continue
            if scenario and ep.get("scenario_id") != scenario:
                continue
            score = ep.get("rubric_score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        if not scores:
            continue

        profile_path = persona_dir / "profile.json"
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            profile = {}

        if role_type:
            haystack = (
                f"{persona_dir.name} {profile.get('specialty','')}"
            ).lower()
            if role_type.lower() not in haystack:
                continue

        out.append({
            "persona_id": persona_dir.name,
            "name": profile.get("name", persona_dir.name),
            "specialty": profile.get("specialty", ""),
            "average_score": round(sum(scores) / len(scores), 4),
            "n_runs": len(scores),
        })

    out.sort(key=lambda r: (-r["average_score"], r["persona_id"]))
    return out[:limit]


@router.get("/{persona_id}")
async def get_persona(request: Request, persona_id: str) -> dict:
    persona = await _store(request).get(persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")
    return persona


@router.get("/{persona_id}/episodes")
async def get_episodes(
    request: Request,
    persona_id: str,
    limit: int = Query(50, ge=1, le=500),
) -> list[dict]:
    """Return the most-recent N episodes for transparency in the UI."""
    store = _store(request)
    persona_dir = store.root / persona_id
    if not persona_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")
    episodes_path = persona_dir / "episodes.jsonl"
    if not episodes_path.exists():
        return []
    lines = [
        line for line in episodes_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    parsed: list[dict] = []
    for line in lines[-limit:]:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return parsed


@router.get("/{persona_id}/patterns")
async def get_patterns(request: Request, persona_id: str) -> dict:
    store = _store(request)
    persona_dir = store.root / persona_id
    if not persona_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")
    patterns_path = persona_dir / "patterns.json"
    if not patterns_path.exists():
        return {"version": 3, "patterns": [], "anti_patterns": [], "archived": []}
    try:
        return json.loads(patterns_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="patterns.json is malformed")


@router.post("/{persona_id}/chat")
async def chat(
    request: Request,
    persona_id: str,
    body: dict = Body(...),
    _auth: None = Depends(require_bearer),
) -> dict:
    """Chat with a persona outside any active run (DESIGN §37).

    Request:
      { "message": "...", "project_id": "?", "run_id": "?" }

    Response:
      { "reply": "...", "persona_id": "...",
        "recall": { "patterns_used": N, "anti_patterns_used": N,
                    "episodes_used": N } }

    Read-only — does not mutate persona memory. project_id / run_id are
    accepted for forward-compat with the §37 spec but not yet wired into
    the prompt; F.4 ships the core chat surface and leaves project/run
    context as a follow-up.
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    store = _store(request)
    persona_dir = store.root / persona_id
    if not persona_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")

    persona = Persona.load(persona_id, store.root)

    llm = _build_chat_llm(request)
    try:
        result = await chat_with_persona(persona, llm, message)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"chat LLM call failed: {e}")

    return {
        "reply": result.reply,
        "persona_id": persona_id,
        "recall": {
            "patterns_used": result.recall.patterns_used,
            "anti_patterns_used": result.recall.anti_patterns_used,
            "episodes_used": result.recall.episodes_used,
        },
    }
