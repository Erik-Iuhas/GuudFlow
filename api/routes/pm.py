"""/api/pm: Jack, the project manager persona (project-flow plan §10.1).

Also holds `remember_*` helpers the plan and project-work routes call after a decision: they
write Jack's episodes (deterministic) and then try, in the background, to distil his patterns
with the existing memory writer. None of it can block or fail the decision.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from api.auth import require_bearer
from core import pm_memory
from core.llm import LLMClient

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pm", tags=["pm"], dependencies=[Depends(require_bearer)])

_background: set[asyncio.Task[Any]] = set()  # Any: tasks hold a strong reference until done


@router.get("")
async def get_pm(request: Request) -> dict[str, Any]:  # Any: JSON
    """Jack: `{persona_id, name, specialty, episodes, patterns, reputation}`."""
    return await asyncio.to_thread(pm_memory.summary, Path(request.app.state.data_root))


def _distill_llm(request: Request) -> LLMClient:
    """The memory writer's model: a test's injected client, else the SOLID tier."""
    injected = getattr(request.app.state, "meta_llm", None)
    if injected is not None:
        return injected
    registry = getattr(request.app.state, "model_registry", None)
    if registry is not None:
        return registry.get_client(registry.resolve("solid"))
    from core.llm import AnthropicClient
    return AnthropicClient()


def schedule_distill(request: Request) -> None:
    """Best effort: distil Jack's patterns in the background. Never raises or blocks."""
    data_root = Path(request.app.state.data_root)

    async def run() -> None:
        try:
            added = await pm_memory.distill(data_root, lambda: _distill_llm(request))
            if added:
                log.info("pm: distilled %d new pattern(s)", added)
        except Exception:  # noqa: BLE001 — distillation is optional
            log.warning("pm: pattern distillation failed", exc_info=True)

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return
    _background.add(task)
    task.add_done_callback(_background.discard)


async def remember_draft(request: Request, project_id: str, draft: Any,  # Any: Draft
                         approved: bool) -> None:
    """Record the draft outcome in Jack's memory, then try to distil. Never raises."""
    data_root = Path(request.app.state.data_root)
    episode = await asyncio.to_thread(pm_memory.record_draft_outcome, data_root, project_id,
                                      draft, approved)
    if episode is not None:
        schedule_distill(request)
