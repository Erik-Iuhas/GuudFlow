"""Unit tests for TaskSummarizer JSON parse + retry + degraded path (M5.5 C1).

Tests mock the LLM to avoid real API calls.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from core.llm import LLMMessage, MockLLMClient, MockTurn
from core.task_summarizer import TaskSummarizer, _DEGRADED_SUMMARY


def _make_mock(texts: list[str]) -> MockLLMClient:
    """MockLLMClient scripted with text-only turns."""
    return MockLLMClient([MockTurn(text=t) for t in texts])


def _summarizer(texts: list[str]) -> TaskSummarizer:
    return TaskSummarizer(llm=_make_mock(texts))


def _messages() -> list[Any]:
    return [
        LLMMessage(role="user", content="implement the login route"),
        LLMMessage(role="assistant", content="[tool_use fs-write] writing routes/login.py"),
        LLMMessage(role="user", content="[tool_result] file written"),
    ]


VALID_RECORD = {
    "summary": "Implemented the JWT login route.",
    "deliverables": ["Added routes/login.py", "Wrote 3 unit tests"],
    "references": ["routes/login.py", "tests/test_login.py"],
    "closed_by_commit": "abc1234",
}


@pytest.mark.asyncio
async def test_valid_json_parse() -> None:
    s = _summarizer([json.dumps(VALID_RECORD)])
    record = await s.summarize(
        task_id="T1",
        task_goal="implement login",
        task_acceptance_criteria=["JWT token returned on success"],
        task_references=["routes/login.py"],
        messages=_messages(),
        commit_sha="abc12345",
        commit_subject="feat: login route",
    )
    assert record.task_id == "T1"
    assert record.summary == "Implemented the JWT login route."
    assert "Added routes/login.py" in record.deliverables
    assert record.closed_by_commit == "abc1234"
    assert not record.is_degraded()


@pytest.mark.asyncio
async def test_malformed_json_retries_once_then_succeeds() -> None:
    """First response is malformed; second is valid."""
    s = _summarizer(["not json at all", json.dumps(VALID_RECORD)])
    record = await s.summarize(
        task_id="T2",
        task_goal="add tests",
        task_acceptance_criteria=[],
        task_references=[],
        messages=_messages(),
        commit_sha="def56789",
        commit_subject="test: add unit tests",
    )
    assert not record.is_degraded()
    assert record.summary == "Implemented the JWT login route."


@pytest.mark.asyncio
async def test_two_failures_produce_degraded_record() -> None:
    """Both attempts fail → degraded record, no exception raised."""
    s = _summarizer(["not json", "also not json"])
    record = await s.summarize(
        task_id="T3",
        task_goal="write docs",
        task_acceptance_criteria=[],
        task_references=["docs/api.md"],
        messages=_messages(),
        commit_sha="fed98765",
        commit_subject="docs: API reference",
    )
    assert record.is_degraded()
    assert record.summary == _DEGRADED_SUMMARY
    assert record.task_id == "T3"
    # References carry through from task metadata.
    assert "docs/api.md" in record.references


@pytest.mark.asyncio
async def test_to_dict_round_trip() -> None:
    s = _summarizer([json.dumps(VALID_RECORD)])
    record = await s.summarize(
        task_id="T1",
        task_goal="implement login",
        task_acceptance_criteria=[],
        task_references=[],
        messages=[],
        commit_sha="abc12345",
        commit_subject="feat: login",
    )
    d = record.to_dict()
    assert d["task_id"] == "T1"
    assert d["summary"] == VALID_RECORD["summary"]
    assert isinstance(d["synthesized_at"], str)
    assert len(d["synthesized_at"]) > 0
