"""Groom the backlog from the roadmap (plan §14.6).

`propose_items()` asks the meta model for the next backlog items, given the
project goal, its roadmap (`data/projects/{id}/context/roadmap.md`, written by
humans) and the current backlog. The proposals are only returned: a human adds
the ones they want, so nothing reaches the backlog on the model's say-so.
Proposals are validated, trimmed and de-duplicated against existing titles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.backlog import KINDS, Backlog
from core.llm.client import LLMClient, LLMMessage

log = logging.getLogger(__name__)

MAX_PROPOSALS = 8

GROOM_SYSTEM = (
    "You keep a software project's backlog groomed. Propose the next few pieces of work "
    "that move the project along its roadmap and are not already on the backlog. Each item "
    "is small enough for one agent team run (a few cards), has a short imperative title and "
    "2-5 concrete, testable acceptance checks. Prefer the roadmap's next milestone. Do not "
    "repeat or rephrase existing items. Call `propose_items` once."
)

PROPOSE_ITEMS_TOOL: dict[str, Any] = {  # Any: JSON schema
    "name": "propose_items",
    "description": "Propose new backlog items for a human to review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array", "maxItems": MAX_PROPOSALS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "acceptance": {"type": "array", "items": {"type": "string"}},
                        "kind": {"type": "string", "enum": sorted(KINDS)},
                        "size": {"type": "string", "enum": ["S", "M", "L"]},
                        "why": {"type": "string"},
                    },
                    "required": ["title", "acceptance"],
                },
            },
        },
        "required": ["items"],
    },
}


@dataclass
class Proposal:
    """One suggested backlog item."""

    title: str
    acceptance: list[str] = field(default_factory=list)
    kind: str = "code"
    size: str = "M"
    why: str = ""


def _message(goal: str, roadmap: str, backlog: Backlog) -> str:
    existing = [f"- {i.id} [{i.status}] {i.title}" for i in backlog.items()
                if i.status != "dropped"][:60]
    return "\n\n".join([
        f"Project goal:\n{goal.strip() or '(none given)'}",
        "=== Roadmap (written by the project's humans) ===\n"
        + (roadmap.strip() or "(no roadmap yet: infer the next steps from the goal)")
        + "\n=== end roadmap ===",
        "=== Current backlog (do not repeat these) ===\n"
        + ("\n".join(existing) or "(empty)") + "\n=== end backlog ===",
    ])


def parse_proposals(raw: Any, backlog: Backlog) -> list[Proposal]:  # Any: tool arguments
    """Validate the model's proposals; drop malformed ones and duplicates of existing titles."""
    known = {i.title.strip().casefold() for i in backlog.items() if i.status != "dropped"}
    out: list[Proposal] = []
    items = raw.get("items") if isinstance(raw, dict) else None
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()[:200]
        if len(title) < 3 or title.casefold() in known:
            continue
        known.add(title.casefold())
        acceptance = [str(a).strip() for a in entry.get("acceptance") or [] if str(a).strip()][:8]
        kind = entry.get("kind") if entry.get("kind") in KINDS else "code"
        size = entry.get("size") if entry.get("size") in ("S", "M", "L") else "M"
        out.append(Proposal(title=title, acceptance=acceptance, kind=str(kind), size=str(size),
                            why=str(entry.get("why") or "").strip()[:400]))
        if len(out) >= MAX_PROPOSALS:
            break
    return out


async def propose_items(llm: LLMClient, data_root: Path, project_id: str,
                        goal: str) -> list[Proposal]:
    """Ask the model for new items. Raises on an LLM error; [] if it proposes nothing usable."""
    backlog = Backlog(data_root, project_id)
    roadmap_path = Path(data_root) / "projects" / project_id / "context" / "roadmap.md"
    roadmap = roadmap_path.read_text(encoding="utf-8") if roadmap_path.is_file() else ""
    response = await llm.complete(system=GROOM_SYSTEM,
                                  messages=[LLMMessage(role="user",
                                                       content=_message(goal, roadmap, backlog))],
                                  tools=[PROPOSE_ITEMS_TOOL], max_tokens=2048)
    call = next((c for c in response.tool_calls if c.name == "propose_items"), None)
    if call is None:
        log.warning("groom: the model answered without calling propose_items")
        return []
    return parse_proposals(call.arguments, backlog)
