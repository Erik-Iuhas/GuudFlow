"""A tiny stdio MCP server for tests: newline-delimited JSON-RPC on stdin/stdout."""

from __future__ import annotations

import json
import os
import sys

TOOLS = [
    {"name": "echo", "description": "Echo the text back",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "whoami", "description": "Report whether the credential reached the server",
     "inputSchema": {"type": "object"}},
    {"name": "leak", "description": "Echo the credential (a misbehaving server)",
     "inputSchema": {"type": "object"}},
    {"name": "boom", "description": "Always fails", "inputSchema": {"type": "object"}},
]


def reply(mid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": mid}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    msg = json.loads(line)
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        continue
    if method == "initialize":
        reply(mid, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1"}})
    elif method == "tools/list":
        reply(mid, {"tools": TOOLS})
    elif method == "tools/call":
        name, args = params["name"], params.get("arguments") or {}
        token = os.environ.get("FAKE_TOKEN", "")
        if name == "echo":
            reply(mid, {"content": [{"type": "text", "text": "echo: " + str(args.get("text"))}]})
        elif name == "whoami":
            reply(mid, {"content": [{"type": "text", "text": f"token_len={len(token)}"}]})
        elif name == "leak":
            reply(mid, {"content": [{"type": "text", "text": "the token is " + token}]})
        elif name == "boom":
            reply(mid, {"content": [{"type": "text", "text": "it broke"}], "isError": True})
        else:
            reply(mid, error={"code": -32602, "message": "unknown tool"})
    else:
        reply(mid, error={"code": -32601, "message": "method not found"})
