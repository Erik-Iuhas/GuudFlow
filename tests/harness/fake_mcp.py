"""Minimal stdio MCP server used by the contract tests.

Speaks just enough MCP (JSON-RPC 2.0, newline-delimited) for opencode:
initialize, tools/list, tools/call, ping. Tools:
  - echo(text)  -> text content
  - pixel()     -> a 1x1 PNG image content block
Run as: python -m tests.harness.fake_mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any

PNG_1X1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

TOOLS = [
    {"name": "echo", "description": "Echo text back.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"]}},
    {"name": "pixel", "description": "Return a 1x1 PNG.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Return the JSON-RPC response for one request (None for notifications)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result: Any = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "fake-mcp", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments") or {}
        if name == "echo":
            result = {"content": [{"type": "text", "text": f"echo: {args.get('text', '')}"}]}
        elif name == "pixel":
            result = {"content": [{"type": "image", "data": PNG_1X1, "mimeType": "image/png"}]}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no tool"}}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": method}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    """Serve on stdin/stdout until EOF."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        reply = handle(json.loads(line))
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
