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
