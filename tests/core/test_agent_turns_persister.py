"""Verify agent_turns.jsonl actually gets written.

Regression: previous version silently swallowed all exceptions in the
record-building path (including a NameError when `drive_result` was
unbound after _drive_agent_run raised through the finally), so the
file was never created on real runs.

These tests exercise the persister callable directly with realistic
record shapes — the multimodal content blocks that come back from
the Anthropic API (lists of {type: text, ...} / {type: tool_use, ...}
/ {type: tool_result, ...}) — to confirm json.dumps doesn't choke.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_persister(tmp_path: Path):
    """Build a persister the same way AgentRunner does, but standalone."""
    from core.runner import AgentRunner

    # We don't need a full runner — just the persister-builder logic. Bind
    # the closure manually by re-using the method against a stub object.
    class _StubRunner:
        run_root = tmp_path

    return AgentRunner._build_agent_turns_persister(_StubRunner())  # type: ignore[arg-type]


def test_persister_writes_simple_string_message(tmp_path):
    persister = _make_persister(tmp_path)
    assert persister is not None

    persister({
        "run_id": "r1",
        "role": "dev",
        "iteration": 0,
        "messages": [{"role": "user", "content": "hello", "tool_calls": None}],
        "final_output": {"kind": "COMMIT", "fields": {"summary": "did it"}},
    })

    log = tmp_path / "agent_turns.jsonl"
    assert log.exists()
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["role"] == "dev"


def test_persister_writes_multimodal_anthropic_content(tmp_path):
    """Anthropic returns assistant turns as a list of typed blocks.
    json.dumps must handle them without choking — the persister uses
    default=str as a safety net."""
    persister = _make_persister(tmp_path)
    assert persister is not None

    # Realistic shape: assistant turn with text + tool_use, then user turn
    # with tool_result, then final assistant text.
    persister({
        "run_id": "r1",
        "role": "architect",
        "iteration": 0,
        "messages": [
            {"role": "user", "content": "build it", "tool_calls": None},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll start with fs-list."},
                    {"type": "tool_use", "id": "tu_1", "name": "fs-list",
                     "input": {"path": "."}},
                ],
                "tool_calls": [{"id": "tu_1", "name": "fs-list",
                                "arguments": {"path": "."}}],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1",
                             "content": "[]"}],
                "tool_calls": None,
            },
            {"role": "assistant", "content": "[COMMIT]\n  summary: done",
             "tool_calls": None},
        ],
        "final_output": {"kind": "COMMIT", "fields": {"summary": "done"}},
    })

    log = tmp_path / "agent_turns.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert len(rows[0]["messages"]) == 4
    # Tool-use block survived the round trip.
    assistant_blocks = rows[0]["messages"][1]["content"]
    assert isinstance(assistant_blocks, list)
    assert assistant_blocks[1]["type"] == "tool_use"
    assert assistant_blocks[1]["name"] == "fs-list"


def test_persister_handles_failed_drive_result_none(tmp_path):
    """When _drive_agent_run raises, drive_result is unbound; the new
    code path uses locals().get(...) to default to None and writes
    final_output as null. Verify that record still serializes."""
    persister = _make_persister(tmp_path)
    persister({
        "run_id": "r1",
        "role": "dev",
        "iteration": 0,
        "failed": True,
        "completed": False,
        "messages": [],
        "final_output": None,
    })
    log = tmp_path / "agent_turns.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["final_output"] is None
    assert rows[0]["failed"] is True


def test_persister_appends_multiple_records(tmp_path):
    persister = _make_persister(tmp_path)
    for i in range(3):
        persister({"run_id": "r1", "role": "dev", "iteration": i,
                   "messages": [], "final_output": None})
    log = tmp_path / "agent_turns.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["iteration"] for r in rows] == [0, 1, 2]


def test_persister_returns_none_when_run_root_uncreatable(tmp_path, monkeypatch):
    """If mkdir fails (read-only fs, permissions), the builder must
    return None and log a warning — never raise."""
    from core.runner import AgentRunner

    class _BadPath:
        def mkdir(self, **_):
            raise OSError("read-only filesystem")

        def __truediv__(self, _):  # never reached
            return self

    class _StubRunner:
        run_root = _BadPath()

    persister = AgentRunner._build_agent_turns_persister(_StubRunner())  # type: ignore[arg-type]
    assert persister is None
