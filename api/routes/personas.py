"""/api/personas — persona roster, memory inspection, chat, and desk CRUD."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from agents.persona import Persona
from agents.persona_chat import chat_with_persona
from api.auth import require_bearer
from core.llm import AnthropicClient, LLMClient


# ── Desk endpoint models (M5.C3 / D6) ─────────────────────────────────────

# Filename must be alphanumeric/hyphens/underscores, end with .md.
_DESK_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_-]+\.md$")
_DESK_CONTENT_MAX = 100_000  # 100 KB


class DeskFileSummary(BaseModel):
    filename: str
    triggers: list[str]
    agents: list[str]
    kind: str
    body_excerpt: str   # first 200 chars of body (no frontmatter)
    bytes: int          # raw file size


class DeskFileWrite(BaseModel):
    filename: str
    content: str
    triggers: list[str] = []
    agents: list[str] = []
    kind: str = ""


def _validate_desk_filename(filename: str) -> None:
    if not _DESK_FILENAME_RE.match(filename):
        raise HTTPException(
            status_code=422,
            detail=(
                "filename must match ^[a-zA-Z0-9_-]+\\.md$ "
                "(no path separators, no leading dots, must end .md)"
            ),
        )


def _persona_desk_dir(store, persona_id: str) -> Path:
    return store.root / persona_id / "desk"


def _parse_desk_file_summary(path: Path) -> DeskFileSummary:
    """Parse a single desk file into a DeskFileSummary. Never raises."""
    from agents.persona_desk import _parse_yaml_frontmatter

    try:
        raw = path.read_text(encoding="utf-8")
        size = path.stat().st_size
    except OSError:
        return DeskFileSummary(
            filename=path.name, triggers=[], agents=[], kind="",
            body_excerpt="(unreadable)", bytes=0,
        )

    fm, body = _parse_yaml_frontmatter(raw)

    def _as_list(v) -> list[str]:
        if not v:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v]
        return [str(v).strip()]

    return DeskFileSummary(
        filename=path.name,
        triggers=_as_list(fm.get("triggers")),
        agents=_as_list(fm.get("agents")),
        kind=str(fm.get("kind", "")),
        body_excerpt=body.strip()[:200],
        bytes=size,
    )


def _build_desk_content(body: DeskFileWrite) -> str:
    """Construct the on-disk markdown content from a DeskFileWrite."""

    def _normalize(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in items:
            s = s.strip().lower()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    triggers = _normalize(body.triggers)
    agents = _normalize(body.agents)
    kind = body.kind.strip()

    fm_lines = ["---"]
    if triggers:
        trigger_str = ", ".join(f'"{t}"' for t in triggers)
        fm_lines.append(f"triggers: [{trigger_str}]")
    else:
        fm_lines.append("triggers: []")
    if agents:
        agent_str = ", ".join(f'"{a}"' for a in agents)
        fm_lines.append(f"agents: [{agent_str}]")
    else:
        fm_lines.append("agents: []")
    if kind:
        fm_lines.append(f'kind: "{kind}"')
    fm_lines.append("---")

    return "\n".join(fm_lines) + "\n" + body.content


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


# ── Desk CRUD endpoints (M5.C3) ─────────────────────────────────────────────


@router.get("/{persona_id}/desk", dependencies=[Depends(require_bearer)])
async def list_desk_files(request: Request, persona_id: str) -> dict:
    """List all desk files for a persona (persona-level only, not project overlay)."""
    store = _store(request)
    persona_dir = store.root / persona_id
    if not persona_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")

    desk_dir = _persona_desk_dir(store, persona_id)
    if not desk_dir.is_dir():
        return {"files": []}

    files = sorted(
        [_parse_desk_file_summary(p) for p in desk_dir.glob("*.md")],
        key=lambda f: f.filename,
    )
    return {"files": [f.model_dump() for f in files]}


@router.post("/{persona_id}/desk", dependencies=[Depends(require_bearer)], status_code=201)
async def create_desk_file(
    request: Request,
    persona_id: str,
    body: DeskFileWrite,
) -> dict:
    """Create a new desk file for a persona.

    Returns 409 if the file already exists (no upsert — use DELETE then POST).
    Returns 413 if content exceeds 100 KB.
    Returns 422 if filename is invalid.
    """
    store = _store(request)
    persona_dir = store.root / persona_id
    if not persona_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")

    _validate_desk_filename(body.filename)

    if len(body.content.encode("utf-8")) > _DESK_CONTENT_MAX:
        raise HTTPException(status_code=413, detail="content exceeds 100 KB limit")

    desk_dir = _persona_desk_dir(store, persona_id)
    desk_dir.mkdir(parents=True, exist_ok=True)

    target = desk_dir / body.filename
    # Path-traversal defence: confirm the resolved path is inside desk_dir.
    try:
        target.resolve().relative_to(desk_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid filename")

    if target.exists():
        raise HTTPException(
            status_code=409,
            detail="file exists; DELETE first or pick a different name",
        )

    target.write_text(_build_desk_content(body), encoding="utf-8")
    return {"filename": body.filename}


@router.delete(
    "/{persona_id}/desk/{filename}",
    dependencies=[Depends(require_bearer)],
    status_code=204,
    response_model=None,
)
async def delete_desk_file(
    request: Request,
    persona_id: str,
    filename: str,
) -> None:
    """Delete a desk file. Returns 204 on success, 404 if not found."""
    store = _store(request)
    persona_dir = store.root / persona_id
    if not persona_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")

    _validate_desk_filename(filename)

    desk_dir = _persona_desk_dir(store, persona_id)
    target = desk_dir / filename
    # Path-traversal defence.
    try:
        target.resolve().relative_to(desk_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid filename")

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"desk file {filename!r} not found")

    target.unlink()
    return None
