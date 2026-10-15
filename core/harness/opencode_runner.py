"""Drive one opencode turn and observe it through the event stream (plan §4.1).

A turn is: subscribe to `/event`, send the prompt asynchronously, then consume
events until the session goes idle. Along the way:

  - every event is handed to `on_event` (the event bridge, plan §2.1),
  - `permission.asked` events are answered through `on_permission`
    (the approval gate, plan §5.1 / §5.9),
  - cost and tokens are accumulated from `message.updated` events.

Transcripts are built from events, never from `GET /session/{id}/message`
(gotcha #10).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from core.harness.opencode_client import OpencodeClient, OpencodeError, text_part

log = logging.getLogger(__name__)

PermissionReply = Literal["once", "always", "reject"]
EventHook = Callable[[dict[str, Any]], None]
PermissionHook = Callable[[dict[str, Any]], Awaitable[PermissionReply]]

CONNECT_TIMEOUT = 15.0


async def reject_all(_ask: dict[str, Any]) -> PermissionReply:
    """Default permission policy: anything not pre-allowed by the ruleset is refused."""
    return "reject"


@dataclass
class TurnStats:
    """What happened during one opencode turn."""

    session_id: str
    cost: float = 0.0
    tokens: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0,
                                                             "reasoning": 0, "cache_read": 0})
    tool_calls: list[tuple[str, str]] = field(default_factory=list)  # (tool, final status)
    permission_asks: int = 0
    errors: list[str] = field(default_factory=list)
    timed_out: bool = False
    # Model calls opencode retried after a failure (429, 5xx, unreachable).
    retries: int = 0
    # Context size of the last model call (input + cache read): what a session carries on.
    last_context: int = 0
    # Programs a shell call in this turn could not find ("bun: command not found").
    missing_programs: set[str] = field(default_factory=set)


# "bash: bun: command not found", "/bin/sh: 1: bun: not found"; zsh: "command not found: bun"
_NOT_FOUND = re.compile(r"(?:^|[\s:])([\w.+-]+): (?:command )?not found\b")
_ZSH_NOT_FOUND = re.compile(r"command not found: ([\w.+-]+)")


def missing_programs(output: str) -> set[str]:
    """Program names a shell output says were not found."""
    text = output or ""
    found = set(_NOT_FOUND.findall(text)) | set(_ZSH_NOT_FOUND.findall(text))
    return found - {"bash", "sh", "zsh", "1"}


class _UsageTracker:
    """Keeps the latest cost/tokens per assistant message and the final state per tool part."""

    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.tool_order: list[str] = []
        self.tools: dict[str, tuple[str, str]] = {}
        self.missing: set[str] = set()

    def observe(self, event: dict[str, Any]) -> None:
        props = event.get("properties") or {}
        kind = event.get("type")
        if kind == "message.updated":
            info = props.get("info") or {}
            if info.get("role") == "assistant" and info.get("id"):
                self.messages[info["id"]] = info
        elif kind == "message.part.updated":
            part = props.get("part") or {}
            if part.get("type") == "tool" and part.get("id"):
                if part["id"] not in self.tools:
                    self.tool_order.append(part["id"])
                state = part.get("state") or {}
                status = state.get("status", "")
                self.tools[part["id"]] = (str(part.get("tool", "")), str(status))
                if part.get("tool") == "bash" and status in ("completed", "error"):
                    self.missing |= missing_programs(str(state.get("output") or state.get("error") or ""))

    def fill(self, stats: TurnStats) -> None:
        stats.cost = sum(float(m.get("cost") or 0.0) for m in self.messages.values())
        for m in self.messages.values():
            tok = m.get("tokens") or {}
            stats.tokens["input"] += int(tok.get("input") or 0)
            stats.tokens["output"] += int(tok.get("output") or 0)
            stats.tokens["reasoning"] += int(tok.get("reasoning") or 0)
            stats.tokens["cache_read"] += int((tok.get("cache") or {}).get("read") or 0)
        stats.tool_calls = [self.tools[i] for i in self.tool_order]
        stats.missing_programs |= self.missing
        if self.messages:
            tok = list(self.messages.values())[-1].get("tokens") or {}
            stats.last_context = (int(tok.get("input") or 0)
                                  + int((tok.get("cache") or {}).get("read") or 0))


async def drive_turn(
    client: OpencodeClient,
    session_id: str,
    text: str,
    *,
    agent: str | None = None,
    on_event: EventHook | None = None,
    on_permission: PermissionHook = reject_all,
    timeout: float | None = 1200.0,
    stall: float | None = None,
    variant: str | None = None,
    done: Callable[[], bool] | None = None,
) -> TurnStats:
    """Send `text` to the session and return once the session is idle again.

    `stall`: abort when no event arrives for this many seconds (plan §11.6: a busy turn
    keeps going however long it takes; a hung one stops). `timeout`: an optional absolute
    cap. Either way the session is aborted and `stats.timed_out` is set; the caller
    decides what that means for the role. `variant` picks one of the model's effort variants
    for this prompt only. `done`, checked after every event, ends the turn early: the session
    is aborted and the turn counts as finished, not timed out.
    """
    stats = TurnStats(session_id=session_id)
    tracker = _UsageTracker()
    events = client.events()
    try:
        try:
            first = await asyncio.wait_for(anext(events), CONNECT_TIMEOUT)
        except (StopAsyncIteration, asyncio.TimeoutError) as exc:
            raise OpencodeError("event stream did not connect") from exc
        _observe(first, tracker, on_event)
        await client.prompt_async(session_id, [text_part(text)], agent=agent, variant=variant)
        try:
            await asyncio.wait_for(
                _consume(client, events, session_id, tracker, stats, on_event, on_permission,
                         stall, done),
                timeout)
        except _Done:
            try:
                await client.abort(session_id)
            except OpencodeError:
                log.warning("abort of %s failed", session_id)
        except _RetriesExhausted:
            log.error("turn stopped: %s", stats.errors[-1])
            try:
                await client.abort(session_id)
            except OpencodeError:
                log.warning("abort of %s failed", session_id)
        except _Stalled:
            stats.timed_out = True
            stats.errors.append(f"turn made no progress for {stall:g}s and was aborted")
            try:
                await client.abort(session_id)
            except OpencodeError:
                log.warning("abort of %s failed", session_id)
        except asyncio.TimeoutError:
            stats.timed_out = True
            stats.errors.append(f"turn exceeded {timeout:.0f}s and was aborted")
            try:
                await client.abort(session_id)
            except OpencodeError:
                log.warning("abort of %s failed", session_id)
    finally:
        await events.aclose()
    tracker.fill(stats)
    return stats


def _observe(event: dict[str, Any], tracker: _UsageTracker, on_event: EventHook | None) -> None:
    tracker.observe(event)
    if on_event is not None:
        try:
            on_event(event)
        except Exception:  # the bridge must never break a turn
            log.exception("event hook failed")


class _Done(Exception):
    """The caller's `done` check said the turn's work is finished."""


class _Stalled(Exception):
    """No event arrived within the stall window."""


class _RetriesExhausted(Exception):
    """opencode kept retrying a failing model call."""


def _max_retries() -> int:
    """How many failed model calls in a row a turn tolerates (GUUDFLOW_MODEL_RETRIES, default 4).
    opencode retries with growing waits and each retry is an event, so without this a turn whose
    model calls keep failing would sit silently until the turn's time limit."""
    import os
    try:
        return max(1, int(os.environ.get("GUUDFLOW_MODEL_RETRIES") or 4))
    except ValueError:
        return 4


async def _consume(client: OpencodeClient, events: Any, session_id: str,
                   tracker: _UsageTracker, stats: TurnStats, on_event: EventHook | None,
                   on_permission: PermissionHook, stall: float | None = None,
                   done: Callable[[], bool] | None = None) -> None:
    # `events` is the async generator from client.events(); typed Any to avoid
    # re-declaring opencode's event union.
    while True:
        try:
            event = (await asyncio.wait_for(anext(events), stall) if stall
                     else await anext(events))
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise _Stalled() from None
        _observe(event, tracker, on_event)
        if done is not None and done():
            raise _Done()
        kind = event.get("type")
        props = event.get("properties") or {}
        if kind == "permission.asked":
            stats.permission_asks += 1
            reply = await on_permission(props)
            await client.reply_permission(str(props.get("id")), reply)
        elif kind == "session.error" and props.get("sessionID") in (session_id, None):
            err = props.get("error") or {}
            stats.errors.append(str((err.get("data") or {}).get("message") or err.get("name")
                                    or "session error"))
        elif kind == "session.status" and props.get("sessionID") == session_id:
            status = props.get("status") or {}
            if status.get("type") == "retry":
                stats.retries += 1
                msg = str(status.get("message") or "model call failed")
                log.warning("model call failed (attempt %s): %s; opencode is retrying",
                            status.get("attempt") or stats.retries, msg[:300])
                if stats.retries >= _max_retries():
                    stats.errors.append(f"model call kept failing ({stats.retries} tries): {msg[:300]}")
                    raise _RetriesExhausted()
        elif kind == "session.idle" and props.get("sessionID") == session_id:
            return
