"""Per-role translator from opencode events to GuudFlow board signals.

Implements the event -> signal table in docs/OPENCODE_HARNESS_PLAN.md section 2.1a.
One ``EventBridge`` is created per role turn stream; it is stateful (it buffers
assistant text and remembers what it already posted) and never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from core.signals import Signal, SignalType

logger = logging.getLogger(__name__)

SKIPPED_TOOLS: frozenset[str] = frozenset({"guudflow_submit_result", "StructuredOutput", "todowrite"})
_TARGET_KEYS: tuple[str, ...] = ("filePath", "path", "pattern", "command", "url", "query")
_TARGET_LIMIT = 80
_SUMMARY_LIMIT = 160


def _collapse(text: str) -> str:
    """Collapse all whitespace runs (including newlines) to single spaces."""
    return " ".join(text.split())


class EventBridge:
    """Translate one role's opencode event stream into board signals."""

    def __init__(
        self,
        role: str,
        post: Callable[[Signal], None],
        *,
        input_limit: int = 2000,
        output_limit: int = 1500,
        text_limit: int = 4000,
    ) -> None:
        """Create a bridge for ``role`` that emits signals through ``post``."""
        self._role = role
        self._post = post
        self._input_limit = input_limit
        self._output_limit = output_limit
        self._text_limit = text_limit
        self._roles: dict[str, str] = {}  # message id -> "assistant" / "user" / ...
        self._texts: dict[str, dict[str, str]] = {}  # message id -> part id -> text
        self._part_types: dict[str, str] = {}
        self._final_parts: set[str] = set()
        self._posted_tools: set[str] = set()
        self._posted_messages: set[str] = set()
        self._last_todos: list[Any] | None = None

    def handle(self, event: dict[str, Any]) -> None:
        """Translate one event; may post zero or more signals. Never raises."""
        try:
            etype = event.get("type")
            props = event.get("properties")
            if not isinstance(props, dict):
                return
            if etype == "message.part.updated":
                self._on_part_updated(props)
            elif etype == "message.part.delta":
                self._on_part_delta(props)
            elif etype == "message.updated":
                self._on_message_updated(props)
            elif etype == "todo.updated":
                self._on_todos(props)
            elif etype == "session.compacted":
                self._emit(SignalType.THINKING, f"{self._role}: context compacted")
            elif etype == "session.error":
                self._on_session_error(props)
        except Exception:  # noqa: BLE001 - bridge must never break the event loop
            logger.exception("event bridge (%s) dropped malformed event", self._role)

    def _emit(self, stype: SignalType, summary: str, detail: str | None = None, priority: str = "normal") -> None:
        self._post(Signal(type=stype, summary=summary, from_agent=self._role, detail=detail, priority=priority))  # type: ignore[arg-type]

    def _on_part_updated(self, props: dict[str, Any]) -> None:
        part = props.get("part")
        if not isinstance(part, dict):
            return
        ptype, pid = part.get("type"), part.get("id")
        if not isinstance(pid, str):
            return
        if isinstance(ptype, str):
            self._part_types[pid] = ptype
        if ptype == "text":
            mid, text = part.get("messageID"), part.get("text")
            if not isinstance(mid, str) or not isinstance(text, str) or self._roles.get(mid) == "user":
                return
            parts = self._texts.setdefault(mid, {})
            if text or pid not in parts:
                parts[pid] = text
            end = part.get("time", {}).get("end") if isinstance(part.get("time"), dict) else None
            if end is not None:
                self._final_parts.add(pid)
        elif ptype == "tool":
            self._on_tool(pid, part)

    def _on_part_delta(self, props: dict[str, Any]) -> None:
        mid, pid, delta = props.get("messageID"), props.get("partID"), props.get("delta")
        if props.get("field") != "text" or not (isinstance(mid, str) and isinstance(pid, str) and isinstance(delta, str)):
            return
        if self._roles.get(mid) == "user" or pid in self._final_parts:
            return
        if self._part_types.get(pid, "text") != "text":
            return
        parts = self._texts.setdefault(mid, {})
        parts[pid] = parts.get(pid, "") + delta

    def _on_message_updated(self, props: dict[str, Any]) -> None:
        info = props.get("info")
        if not isinstance(info, dict):
            return
        mid, role = info.get("id"), info.get("role")
        if not isinstance(mid, str) or not isinstance(role, str):
            return
        self._roles[mid] = role
        if role != "assistant":
            self._texts.pop(mid, None)
            return
        time = info.get("time")
        if not isinstance(time, dict) or time.get("completed") is None or mid in self._posted_messages:
            return
        text = "".join(self._texts.get(mid, {}).values()).strip()
        if not text:
            return
        self._posted_messages.add(mid)
        self._emit(SignalType.THINKING, _collapse(text)[:_SUMMARY_LIMIT], text[: self._text_limit])

    def _on_tool(self, pid: str, part: dict[str, Any]) -> None:
        tool, state = part.get("tool"), part.get("state")
        if not isinstance(tool, str) or not isinstance(state, dict) or tool in SKIPPED_TOOLS:
            return
        status = state.get("status")
        if status not in ("completed", "error") or pid in self._posted_tools:
            return
        self._posted_tools.add(pid)
        raw_input = state.get("input")
        target = ""
        if isinstance(raw_input, dict):
            for key in _TARGET_KEYS:
                value = raw_input.get(key)
                if value:
                    target = _collapse(str(value))[:_TARGET_LIMIT]
                    break
        time = state.get("time") if isinstance(state.get("time"), dict) else {}
        start, end = time.get("start"), time.get("end")
        duration = end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
        detail: dict[str, Any] = {
            "tool": tool,
            "status": status,
            "input": json.dumps(raw_input, default=str)[: self._input_limit],
        }
        if status == "completed":
            detail["output"] = str(state.get("output", ""))[: self._output_limit]
        else:
            detail["error"] = str(state.get("error", ""))[: self._output_limit]
        detail["duration_ms"] = duration
        verdict = "ok" if status == "completed" else "error"
        summary = " ".join(p for p in (f"{self._role}: {tool}", target, f"→ {verdict}") if p)
        self._emit(SignalType.TOOL_CALL, summary, json.dumps(detail, default=str))

    def _on_todos(self, props: dict[str, Any]) -> None:
        todos = props.get("todos")
        if not isinstance(todos, list) or todos == self._last_todos:
            return
        self._last_todos = json.loads(json.dumps(todos, default=str))
        done = sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed")
        self._emit(SignalType.AGENT_TODO, f"{self._role}: {len(todos)} todos ({done} done)", json.dumps(todos, default=str))

    def _on_session_error(self, props: dict[str, Any]) -> None:
        error = props.get("error")
        error = error if isinstance(error, dict) else {}
        data = error.get("data")
        message = data.get("message") if isinstance(data, dict) else None
        message = message or error.get("name") or "unknown error"
        self._emit(SignalType.THINKING, f"{self._role}: error — {message}", priority="high")
