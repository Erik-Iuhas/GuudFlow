"""Web tools — `web:*`. Uncredentialed.

DESIGN §31. `web:search` and `web:fetch` are baseline read tools.

Stage 4 ships these as STUBS that:
  - return deterministic placeholder responses for tests
  - accept an optional injected handler so production can plug in a real
    search backend or HTTP client without changing the BaseTool contract

The runner (Stage 5) will inject real handlers — search via a configured
provider's broker handler, fetch via an HTTP client with allow-listing.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from agents.tools.base import BaseTool


def _err(msg: str) -> dict[str, Any]:
    return {"error": msg}


SearchHandler = Callable[[str, int], Awaitable[list[dict[str, Any]]]]
FetchHandler = Callable[[str], Awaitable[dict[str, Any]]]


class WebSearch(BaseTool):
    """Search the web. The actual call is performed by an injected handler;
    in dev mode without a handler, returns a clear `no_handler` result."""

    name = "web-search"
    priority = 78
    when_to_use = "look something up on the public web"
    description = "Search the public web for the given query. Returns ranked results."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    }

    def __init__(self, handler: SearchHandler | None = None):
        self.handler = handler

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        limit = int(arguments.get("limit", 5))
        if not query or not isinstance(query, str):
            return _err("missing required argument: query")
        if self.handler is None:
            return {
                "ok": False,
                "no_handler": True,
                "message": "no search handler configured for this run",
                "query": query,
                "results": [],
            }
        try:
            results = await self.handler(query, limit)
        except Exception as e:
            return _err(f"search failed: {e}")
        return {"ok": True, "query": query, "limit": limit, "results": results}


class WebFetch(BaseTool):
    """Fetch a public URL. No credentials. Anything credentialed must go
    through the broker as `mcp:*` or a similar scoped tool."""

    name = "web-fetch"
    priority = 75
    when_to_use = "fetch a public URL (no credentials)"
    description = "Fetch a public URL (uncredentialed). Returns text body."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
        },
        "required": ["url"],
    }

    def __init__(self, handler: FetchHandler | None = None):
        self.handler = handler

    async def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = arguments.get("url")
        if not url or not isinstance(url, str):
            return _err("missing required argument: url")
        if not (url.startswith("http://") or url.startswith("https://")):
            return _err("url must start with http:// or https://")
        if self.handler is None:
            return {
                "ok": False,
                "no_handler": True,
                "message": "no fetch handler configured for this run",
                "url": url,
            }
        try:
            result = await self.handler(url)
        except Exception as e:
            return _err(f"fetch failed: {e}")
        return {"ok": True, "url": url, **result}
