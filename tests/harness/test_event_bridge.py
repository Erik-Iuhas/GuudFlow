"""Tests for core.harness.event_bridge (opencode events -> board signals)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from core.harness.event_bridge import EventBridge
from core.signals import Signal, SignalType

FIXTURE = Path(__file__).parent / "fixtures" / "events_builder_turn.jsonl"


def _bridge(**kw: int) -> tuple[EventBridge, list[Signal]]:
    out: list[Signal] = []
    return EventBridge("builder", out.append, **kw), out


def _tool(pid: str, tool: str, status: str, inp: dict[str, Any] | None = None, **state: Any) -> dict[str, Any]:
    return {
        "type": "message.part.updated",
        "properties": {
            "part": {"type": "tool", "id": pid, "tool": tool, "messageID": "m1",
                     "state": {"status": status, "input": inp or {}, **state}},
        },
    }


def _msg(mid: str, role: str, completed: int | None = None) -> dict[str, Any]:
    time: dict[str, Any] = {"created": 1}
    if completed is not None:
        time["completed"] = completed
    return {"type": "message.updated", "properties": {"info": {"id": mid, "role": role, "time": time}}}


def _text_part(mid: str, pid: str, text: str, end: int | None = None) -> dict[str, Any]:
    part: dict[str, Any] = {"type": "text", "id": pid, "messageID": mid, "text": text}
    if end is not None:
        part["time"] = {"start": 1, "end": end}
    return {"type": "message.part.updated", "properties": {"part": part}}


def _delta(mid: str, pid: str, delta: str) -> dict[str, Any]:
    return {"type": "message.part.delta",
            "properties": {"messageID": mid, "partID": pid, "field": "text", "delta": delta}}


def test_fixture_replay() -> None:
    bridge, out = _bridge()
    for line in FIXTURE.read_text().splitlines():
        bridge.handle(json.loads(line))
    assert all(s.from_agent == "builder" for s in out)

    tools = [s for s in out if s.type == SignalType.TOOL_CALL]
    assert [s.summary for s in tools] == [
        "builder: write greet.py → ok",
        "builder: read missing.py → error",
        "builder: bash git status → ok",
    ]
    assert "File not found" in (tools[1].detail or "")
    assert json.loads(tools[0].detail or "")["duration_ms"] is not None
    assert not any("submit_result" in s.summary or "todowrite" in s.summary for s in out)

    todos = [s for s in out if s.type == SignalType.AGENT_TODO]
    assert len(todos) == 1 and "2 todos (0 done)" in todos[0].summary

    thinking = [s.summary for s in out if s.type == SignalType.THINKING]
    assert thinking == ["I'll start by planning the work.", "Writing the module now.", "Done."]
    assert not any("build greet.py" in (s.summary + (s.detail or "")) for s in out)


def test_duplicate_completion_posts_once() -> None:
    bridge, out = _bridge()
    for status in ("pending", "running", "completed", "completed", "error"):
        bridge.handle(_tool("p1", "grep", status, {"pattern": "foo"}, output="x"))
    assert len(out) == 1 and out[0].summary == "builder: grep foo → ok"


def test_todo_dedupe() -> None:
    bridge, out = _bridge()
    a = [{"content": "a", "status": "completed"}, {"content": "b", "status": "pending"}]
    ev = {"type": "todo.updated", "properties": {"todos": a}}
    bridge.handle(ev)
    bridge.handle(copy.deepcopy(ev))
    assert len(out) == 1 and out[0].summary == "builder: 2 todos (1 done)"
    bridge.handle({"type": "todo.updated", "properties": {"todos": a[:1]}})
    assert len(out) == 2 and out[1].summary == "builder: 1 todos (1 done)"
    assert json.loads(out[1].detail or "") == a[:1]


def test_truncation_limits() -> None:
    bridge, out = _bridge(input_limit=20, output_limit=10, text_limit=30)
    bridge.handle(_tool("p1", "bash", "completed", {"command": "x" * 200}, output="y" * 100))
    detail = json.loads(out[0].detail or "")
    assert len(detail["input"]) == 20 and len(detail["output"]) == 10
    assert out[0].summary == "builder: bash " + "x" * 80 + " → ok"

    bridge.handle(_msg("m2", "assistant"))
    bridge.handle(_text_part("m2", "t1", "word " * 100))
    bridge.handle(_msg("m2", "assistant", completed=5))
    assert len(out[1].detail or "") == 30 and "\n" not in out[1].summary


def test_summary_is_160_chars_single_line() -> None:
    bridge, out = _bridge()
    bridge.handle(_msg("m2", "assistant"))
    bridge.handle(_text_part("m2", "t1", "line one\n\n  line   two " + "z" * 300))
    bridge.handle(_msg("m2", "assistant", completed=1))
    assert len(out[0].summary) == 160 and out[0].summary.startswith("line one line two z")


def test_text_posted_once_and_prefers_updated_part() -> None:
    bridge, out = _bridge()
    bridge.handle(_msg("m2", "assistant"))
    bridge.handle(_delta("m2", "t1", "Hel"))
    bridge.handle(_delta("m2", "t1", "lo"))
    bridge.handle(_text_part("m2", "t1", "Hello there", end=2))
    bridge.handle(_delta("m2", "t1", "!!"))  # ignored: part already final text
    bridge.handle(_msg("m2", "assistant"))  # not completed -> nothing
    assert out == []
    bridge.handle(_msg("m2", "assistant", completed=9))
    bridge.handle(_msg("m2", "assistant", completed=9))
    assert [s.summary for s in out] == ["Hello there"]


def test_user_text_ignored() -> None:
    bridge, out = _bridge()
    bridge.handle(_msg("u1", "user"))
    bridge.handle(_text_part("u1", "t0", "secret prompt"))
    bridge.handle(_delta("u1", "t0", "more"))
    bridge.handle(_msg("u1", "user", completed=3))
    assert out == []


def test_session_error_and_compacted() -> None:
    bridge, out = _bridge()
    bridge.handle({"type": "session.compacted", "properties": {}})
    bridge.handle({"type": "session.error", "properties": {"error": {"name": "X", "data": {"message": "boom"}}}})
    bridge.handle({"type": "session.error", "properties": {"error": {"name": "ApiError"}}})
    assert [s.summary for s in out] == [
        "builder: context compacted", "builder: error — boom", "builder: error — ApiError",
    ]
    assert [s.priority for s in out] == ["normal", "high", "high"]
    assert all(s.type == SignalType.THINKING for s in out)


def test_malformed_events_do_not_raise() -> None:
    bridge, out = _bridge()
    bad: list[Any] = [
        {}, {"type": None}, {"type": "message.part.updated"}, {"type": "message.part.updated", "properties": []},
        {"type": "message.part.updated", "properties": {"part": "x"}},
        {"type": "message.part.updated", "properties": {"part": {"type": "tool", "id": "p", "tool": 5, "state": 3}}},
        {"type": "message.part.updated", "properties": {"part": {"type": "tool", "id": "p", "tool": "bash",
                                                                 "state": {"status": "completed", "input": 7, "time": "x"}}}},
        {"type": "message.part.delta", "properties": {"delta": 5}},
        {"type": "message.updated", "properties": {"info": {"id": 1, "role": []}}},
        {"type": "message.updated", "properties": {"info": {"id": "m", "role": "assistant", "time": "x"}}},
        {"type": "todo.updated", "properties": {"todos": "nope"}},
        {"type": "session.error", "properties": {"error": "str"}},
    ]
    for ev in bad:
        bridge.handle(ev)  # must not raise
    assert all(isinstance(s, Signal) for s in out)


def test_post_failure_does_not_raise() -> None:
    def boom(_: Signal) -> None:
        raise RuntimeError("post failed")

    EventBridge("builder", boom).handle({"type": "session.compacted", "properties": {}})


def test_events_not_mutated() -> None:
    bridge, _ = _bridge()
    events = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    snapshot = copy.deepcopy(events)
    for ev in events:
        bridge.handle(ev)
    assert events == snapshot
