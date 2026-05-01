"""Protocol retry budget — bumped from 1 to max_protocol_retries (default 3).

Tests:
  - Two bad blocks then a valid VERIFY → runner returns the valid block.
  - Four bad blocks → runner returns synthetic VERIFY FAIL instead of raising.
  - Each bad attempt posts a PROTOCOL_PARSE_FAIL signal on the board.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import protocol
from core.llm import MockLLMClient
from core.llm.client import MockTurn
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import SignalType


def _project(agents: list[AgentConfig]) -> ProjectConfig:
    return ProjectConfig(
        project_id="p1",
        goal="do a thing",
        agents=agents,
        rbac={},
        max_iterations=3,
    )


def _reviewer_cfg(**overrides) -> AgentConfig:
    base = dict(
        role="code_reviewer",
        description="reviews code",
        is_reviewer=True,
        max_protocol_retries=3,
        max_tool_iterations=20,
    )
    base.update(overrides)
    return AgentConfig(**base)


def _bad_turn(text: str = "I did stuff.") -> MockTurn:
    """LLM response with NO trailing protocol block."""
    return MockTurn(text=text, stop_reason="end_turn")


def _good_verify_turn(result: str = "PASS") -> MockTurn:
    block = f"[VERIFY]\n  result: {result}\n  detail: looks good"
    return MockTurn(text=block, stop_reason="end_turn")


@pytest.mark.asyncio
async def test_two_bad_then_valid_returns_block(tmp_path: Path):
    """2 bad → 1 valid: runner returns the valid block, does NOT raise."""
    cfg = _reviewer_cfg()
    llm = MockLLMClient([_bad_turn(), _bad_turn(), _good_verify_turn("PASS")])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r1",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    block = await runner._generic_tool_use_loop(agent, cfg)

    assert isinstance(block, protocol.CompactBlock)
    assert block.kind == "VERIFY"
    assert block.fields["result"] == "PASS"


@pytest.mark.asyncio
async def test_four_bad_returns_synthetic_fail(tmp_path: Path):
    """Exhausting the retry budget returns VERIFY FAIL instead of raising."""
    cfg = _reviewer_cfg(max_protocol_retries=3)
    # Queue enough bad turns so the script doesn't exhaust prematurely.
    llm = MockLLMClient([_bad_turn()] * 20)
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r2",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    block = await runner._generic_tool_use_loop(agent, cfg)

    assert isinstance(block, protocol.CompactBlock)
    assert block.kind == "VERIFY"
    assert block.fields["result"] == "FAIL"
    assert cfg.role in block.fields.get("detail", "")


@pytest.mark.asyncio
async def test_each_bad_attempt_posts_protocol_parse_fail_signal(tmp_path: Path):
    """Each failed parse attempt posts a PROTOCOL_PARSE_FAIL signal."""
    cfg = _reviewer_cfg()
    llm = MockLLMClient([_bad_turn(), _bad_turn(), _good_verify_turn()])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r3",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    await runner._generic_tool_use_loop(agent, cfg)

    fail_signals = [s for s in runner.workspace.board if s.type is SignalType.PROTOCOL_PARSE_FAIL]
    assert len(fail_signals) == 2  # two bad attempts before the good one


@pytest.mark.asyncio
async def test_exhaustion_posts_max_signals(tmp_path: Path):
    """Exhausting budget posts max_protocol_retries PROTOCOL_PARSE_FAIL signals."""
    cfg = _reviewer_cfg(max_protocol_retries=3)
    llm = MockLLMClient([_bad_turn()] * 20)
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r4",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    await runner._generic_tool_use_loop(agent, cfg)

    fail_signals = [s for s in runner.workspace.board if s.type is SignalType.PROTOCOL_PARSE_FAIL]
    # 3 retries means 3 PROTOCOL_PARSE_FAIL signals
    assert len(fail_signals) == 3
