"""Tests for core/quip_engine.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import MockLLMClient, MockTurn
from core.quip_engine import Quip, QuipEngine
from core.scheduler import SchedulerResult, SchedulerStatus
from core.workspace import Workspace


@pytest.fixture
def workspace() -> Workspace:
    return Workspace(run_id="r1", goal="x", completed_agents=["developer", "reviewer"])


async def test_quip_on_run_complete_persists_to_disk(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([MockTurn(text="ship it")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "quip.json", persona_name="Aria")
    result = SchedulerResult(
        status=SchedulerStatus.COMPLETE,
        completed=["developer", "reviewer"],
        failed=[],
        spawn_counts={"developer": 1, "reviewer": 1},
    )
    await engine.on_run_complete(workspace, result)
    assert (tmp_path / "quip.json").exists()
    data = json.loads((tmp_path / "quip.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["text"] == "ship it"
    assert data[0]["event"] == "run_complete"
    assert data[0]["persona"] == "Aria"


async def test_quip_uses_fast_tier_model(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([MockTurn(text="zip")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_run_complete(workspace, result)
    assert llm.calls[0]["model"] == QuipEngine.FAST_TIER_MODEL


async def test_quip_max_tokens_capped(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([MockTurn(text="x")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_run_complete(workspace, result)
    assert llm.calls[0]["max_tokens"] == QuipEngine.MAX_TOKENS


async def test_quip_failure_does_not_crash(tmp_path: Path, workspace: Workspace):
    """If the LLM call raises, the quip log records the failure
    (as best-effort flavour text) rather than propagating."""
    class BadLLM:
        async def complete(self, **kwargs):
            raise RuntimeError("LLM down")
    engine = QuipEngine(llm=BadLLM(), log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_run_complete(workspace, result)
    data = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert "failed" in data[0]["text"].lower()


async def test_on_event_appends_quip(tmp_path: Path):
    llm = MockLLMClient([MockTurn(text="zinger")])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    await engine.on_event("merge conflict on shared.py", event="conflict")
    data = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert data[0]["event"] == "conflict"


async def test_quips_accumulate(tmp_path: Path, workspace: Workspace):
    llm = MockLLMClient([
        MockTurn(text="a"), MockTurn(text="b"), MockTurn(text="c"),
    ])
    engine = QuipEngine(llm=llm, log_path=tmp_path / "q.json")
    result = SchedulerResult(SchedulerStatus.COMPLETE, [], [], {})
    await engine.on_iteration_complete(workspace, iteration=1)
    await engine.on_iteration_complete(workspace, iteration=2)
    await engine.on_run_complete(workspace, result)
    data = json.loads((tmp_path / "q.json").read_text(encoding="utf-8"))
    assert [q["text"] for q in data] == ["a", "b", "c"]
