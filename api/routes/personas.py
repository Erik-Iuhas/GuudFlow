"""/api/personas — read-only persona roster + memory inspection."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request


router = APIRouter(prefix="/api/personas", tags=["personas"])


def _store(request: Request):
    return request.app.state.persona_store


@router.get("")
async def list_personas(request: Request) -> list[dict]:
    return await _store(request).list()


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
