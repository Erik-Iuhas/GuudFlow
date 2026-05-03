"""Max-tokens continuation in `_generic_tool_use_loop`.

Diagnosed in run_2b1c86d56f: Codestral kept hitting the 8192 output cap
trying to emit "tool_use + closing protocol block" in one turn, and the
old recovery path nudged "split into smaller calls" — which Codestral
ignored, looping until the iteration cap.

The new behaviour:
  - First N truncations: send a "continue from where you left off"
    prompt so the model can finish a long emission.
  - After the cap (MAX_TOKENS_CONTINUATION_CAP = 2): fall back to the
    older "split smaller" recovery.

Per-model max_output_tokens (Mistral=16384) is the structural fix; this
test covers the runtime fallback when the model still trips the cap.
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
        project_id="p_continuation",
        goal="ship a thing",
        agents=agents,
        rbac={},
        max_iterations=3,
    )


def _reviewer_cfg() -> AgentConfig:
    return AgentConfig(
        role="code_reviewer",
        description="reviews code",
        is_reviewer=True,
        max_protocol_retries=3,
        max_tool_iterations=20,
    )


def _truncated_turn(text: str = "[COMMIT]\n  summary: half-written") -> MockTurn:
    """An emission that hit the output cap mid-protocol-block."""
    return MockTurn(text=text, stop_reason="max_tokens")


def _verify_turn(result: str = "PASS") -> MockTurn:
    block = f"[VERIFY]\n  result: {result}\n  detail: looks good"
    return MockTurn(text=block, stop_reason="end_turn")


@pytest.mark.asyncio
async def test_first_truncation_triggers_continuation_then_succeeds(tmp_path: Path):
    """Truncated turn → continuation prompt added → model finishes
    cleanly on the next turn → loop returns the parsed block. The
    continuation must NOT just bail to 'split into smaller calls'."""
    cfg = _reviewer_cfg()
    llm = MockLLMClient([_truncated_turn(), _verify_turn("PASS")])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r_cont_1",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    block = await runner._generic_tool_use_loop(agent, cfg)

    assert isinstance(block, protocol.CompactBlock)
    assert block.kind == "VERIFY"
    assert block.fields["result"] == "PASS"

    # Truncation was logged so a human can spot the recovery in the board.
    parse_fails = [
        s for s in runner.workspace.board if s.type is SignalType.PROTOCOL_PARSE_FAIL
    ]
    assert len(parse_fails) == 1
    assert "truncated" in parse_fails[0].detail.lower()
    assert "continuations_used=0" in parse_fails[0].detail


@pytest.mark.asyncio
async def test_continuation_prompt_says_continue_not_split(tmp_path: Path):
    """First truncation must use the CONTINUE prompt, not the SPLIT
    fallback. SPLIT loses partial progress and was the failure mode
    in run_2b1c86d56f — Codestral kept getting told to split and kept
    truncating instead of being told to finish what it started."""
    cfg = _reviewer_cfg()
    llm = MockLLMClient([_truncated_turn(), _verify_turn("PASS")])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r_cont_2",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    await runner._generic_tool_use_loop(agent, cfg)

    # The mock client recorded every system+messages tuple it saw.
    # The second call's messages must include the CONTINUE-style nudge
    # (not the SPLIT one).
    assert len(llm.calls) == 2
    messages_on_second_call = llm.calls[1]["messages"]
    last_user = next(m for m in reversed(messages_on_second_call) if m.role == "user")
    assert "CONTINUE" in str(last_user.content)
    assert "Do NOT repeat what you already wrote" in str(last_user.content)


@pytest.mark.asyncio
async def test_third_truncation_falls_back_to_split_recovery(tmp_path: Path):
    """After MAX_TOKENS_CONTINUATION_CAP (2) consecutive truncations,
    the runner switches to the conservative SPLIT recovery message.
    Continuation isn't working — the pattern itself is too large."""
    cfg = _reviewer_cfg()
    llm = MockLLMClient([
        _truncated_turn(),  # continuation #1
        _truncated_turn(),  # continuation #2
        _truncated_turn(),  # cap exhausted → SPLIT message
        _verify_turn("PASS"),
    ])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r_cont_3",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    block = await runner._generic_tool_use_loop(agent, cfg)

    assert isinstance(block, protocol.CompactBlock)
    assert block.kind == "VERIFY"

    # On the 4th LLM call (after the 3rd truncation) the user message
    # must be the SPLIT fallback, not another CONTINUE.
    assert len(llm.calls) == 4
    last_user = next(
        m for m in reversed(llm.calls[3]["messages"]) if m.role == "user"
    )
    content = str(last_user.content)
    assert "SPLIT" in content
    assert "split the work into smaller individual calls" in content


@pytest.mark.asyncio
async def test_truncation_signals_track_continuation_count(tmp_path: Path):
    """Each PROTOCOL_PARSE_FAIL signal includes the continuation count
    so the operator can see in the board how many recoveries happened
    before the model either succeeded or fell back to SPLIT."""
    cfg = _reviewer_cfg()
    llm = MockLLMClient([
        _truncated_turn(),
        _truncated_turn(),
        _verify_turn("PASS"),
    ])
    runner = AgentRunner(
        project=_project([cfg]),
        run_id="r_cont_4",
        run_root=tmp_path / "run",
        llm=llm,
    )
    runner._build_agents()
    agent = runner.agents[cfg.role]
    await runner._generic_tool_use_loop(agent, cfg)

    parse_fails = [
        s for s in runner.workspace.board if s.type is SignalType.PROTOCOL_PARSE_FAIL
    ]
    assert len(parse_fails) == 2
    # First truncation: 0 continuations used yet, second: 1.
    assert "continuations_used=0" in parse_fails[0].detail
    assert "continuations_used=1" in parse_fails[1].detail
