"""Scripted OpenAI-compatible LLM for offline harness tests.

opencode is pointed at this server through a provider `baseURL`. Each chat
request pops the next scripted turn and streams it back exactly as the
OpenAI chat-completions SSE format does, so opencode's real tool-calling,
permission and structured-output code paths run without any API key.

Routing: several agents can share one FakeLLM. `route(marker, turns)` sends a
request to that queue when `marker` appears anywhere in the request's messages
(system prompt or brief). Unmatched requests use the default queue.

Requests without tools (session titles, compaction summaries) never consume
script turns; they get a short canned reply.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


@dataclass
class ToolCall:
    """The model calls `name` with `args`."""

    name: str
    args: dict[str, Any]


@dataclass
class Text:
    """The model replies with plain text and stops."""

    text: str


@dataclass
class Turn:
    """One model response: any tool calls (in order), then optional text."""

    calls: list[ToolCall] = field(default_factory=list)
    text: str = ""


Scripted = ToolCall | Text | Turn | Callable[[dict[str, Any]], "ToolCall | Text | Turn"]

FALLBACK_TEXT = "(fake llm: script exhausted)"


def _as_turn(item: ToolCall | Text | Turn) -> Turn:
    if isinstance(item, Turn):
        return item
    if isinstance(item, ToolCall):
        return Turn(calls=[item])
    return Turn(text=item.text)


def _message_text(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(c.get("text", "") for c in content if isinstance(c, dict))
    return "\n".join(parts)


class FakeLLM:
    """In-process scripted LLM server. Use as a context manager."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._default: list[Scripted] = []
        self._routes: list[tuple[str, list[Scripted]]] = []
        self.requests: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── scripting ──────────────────────────────────────────────────────────
    def script(self, *turns: Scripted) -> "FakeLLM":
        """Append turns to the default queue."""
        with self._lock:
            self._default.extend(turns)
        return self

    def route(self, marker: str, *turns: Scripted) -> "FakeLLM":
        """Append turns to the queue used when `marker` appears in a request."""
        with self._lock:
            for key, queue in self._routes:
                if key == marker:
                    queue.extend(turns)
                    return self
            self._routes.append((marker, list(turns)))
        return self

    def remaining(self, marker: str | None = None) -> int:
        """Number of unconsumed turns in a queue."""
        with self._lock:
            if marker is None:
                return len(self._default)
            return next((len(q) for k, q in self._routes if k == marker), 0)

    def tool_requests(self) -> list[dict[str, Any]]:
        """Logged requests that offered tools (i.e. real agent steps)."""
        return [r for r in self.requests if r["tools"]]

    def _next(self, body: dict[str, Any]) -> Turn:
        text = _message_text(body)
        with self._lock:
            queue = next((q for k, q in self._routes if k in text), self._default)
            item = queue.pop(0) if queue else Text(FALLBACK_TEXT)
        if callable(item) and not isinstance(item, (ToolCall, Text, Turn)):
            item = item(body)
        return _as_turn(item)

    # ── server ─────────────────────────────────────────────────────────────
    @property
    def base_url(self) -> str:
        """OpenAI-compatible base URL (ends in /v1)."""
        assert self._server is not None, "FakeLLM not started"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> "FakeLLM":
        """Start serving on an ephemeral port in a daemon thread."""
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence stderr
                pass

            def do_GET(self) -> None:
                self._json({"object": "list", "data": [{"id": "fake-model", "object": "model"}]})

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
                if tools:
                    turn = fake._next(body)
                else:
                    turn = Turn(text="Fake session")
                with fake._lock:
                    fake.requests.append({
                        "tools": tools,
                        "tool_specs": body.get("tools") or [],
                        "tool_choice": body.get("tool_choice"),
                        "messages": body.get("messages", []),
                        "reasoning_effort": body.get("reasoning_effort"),
                        "turn": turn,
                    })
                if body.get("stream", True):
                    self._stream(turn)
                else:
                    self._json(_completion(turn))

            def _json(self, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _stream(self, turn: Turn) -> None:
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for chunk in _chunks(turn):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Shut the server down."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> "FakeLLM":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def _base() -> dict[str, Any]:
    return {"id": "fake", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": "fake-model"}


def _chunks(turn: Turn) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    delta: dict[str, Any] = {"role": "assistant"}
    if turn.text:
        delta["content"] = turn.text
    if turn.calls:
        delta["tool_calls"] = [
            {"index": i, "id": f"call_{time.time_ns()}_{i}", "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.args)}}
            for i, c in enumerate(turn.calls)
        ]
    chunks.append({**_base(), "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
    finish = "tool_calls" if turn.calls else "stop"
    chunks.append({**_base(), "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                   "usage": _USAGE})
    return chunks


def _completion(turn: Turn) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
    if turn.calls:
        message["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": c.name, "arguments": json.dumps(c.args)}}
            for i, c in enumerate(turn.calls)
        ]
    return {"id": "fake", "object": "chat.completion", "created": int(time.time()),
            "model": "fake-model", "usage": _USAGE,
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if turn.calls else "stop"}]}


def fake_provider_config(base_url: str) -> dict[str, Any]:
    """opencode `provider` + `model` config entries that point at a FakeLLM."""
    return {
        "model": "fake/fake-model",
        "small_model": "fake/fake-model",
        "provider": {
            "fake": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Fake",
                "options": {"baseURL": base_url, "apiKey": "fake-key"},
                "models": {
                    "fake-model": {
                        "name": "fake-model",
                        "tool_call": True,
                        "attachment": True,
                        "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                        "limit": {"context": 200000, "output": 8000},
                    }
                },
            }
        },
    }
