"""desk-search tool — explicit keyword search of the persona's desk (M2).

Complements trigger-based auto-injection with an on-demand lookup for
knowledge files that didn't match the current task's triggers.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from agents.tools.base import BaseTool

if TYPE_CHECKING:
    from agents.persona_desk import DeskIndex


class DeskSearchTool(BaseTool):
    name = "desk-search"
    description = "Search your persona desk for relevant notes. Returns ranked snippets within token budget."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free-text keyword query"},
            "max_tokens": {"type": "integer", "description": "Max tokens to return (default 800)", "default": 800},
        },
        "required": ["query"],
    }

    def __init__(self, desk_index: "DeskIndex | None", role: str = ""):
        self._index = desk_index
        self._role = role

    def aci_doc(self) -> dict[str, str]:
        return {
            "purpose": "Search persona desk by keyword.",
            "args": "query: str (free text), max_tokens: int = 800",
            "emits": "ACTION desk-search → snippets",
            "errors": "DESK_EMPTY, NO_MATCHES",
            "example": 'ACTION desk-search query="rate limit jwt"',
        }

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": "query is required"}

        max_tokens = int(arguments.get("max_tokens", 800))

        if self._index is None or self._index.is_empty():
            return {"error": "DESK_EMPTY", "results": []}

        matches = self._index.search(query, max_tokens=max_tokens, role=self._role)
        if not matches:
            return {"error": "NO_MATCHES", "results": []}

        results = []
        for df, snippet in matches:
            entry: dict[str, Any] = {
                "name": df.name,
                "priority": df.priority,
                "kind": df.kind,
                "layer": df.layer,
                "snippet": snippet,
            }
            if df.handoff_from:
                entry["handoff_from"] = df.handoff_from
                if df.handoff_note:
                    entry["handoff_note"] = df.handoff_note
            results.append(entry)

        return {"results": results, "count": len(results)}
