"""Bounded peer-answer loop in `_generic_peer_answer`.

Diagnosed in run_6b8d0190b2: QA asked game_logic_dev to explain the
implementation. game_logic_dev's respond turn called `fs-list` instead
of emitting `[ANSWER]` text. The single-LLM-call function returned
`response.text` (empty), the dispatcher posted `answer: ""`, and QA
got blocked on a silent empty string.

The new loop:
  - Up to PEER_ANSWER_MAX_ITERATIONS (3) tool calls so the responder
    can verify a detail before answering.
  - Inject the responder's OWN recent commits + last COMMIT block
    so they can answer from "what I just shipped" without
    re-exploring the codebase.
  - On exhaustion: synthesize a fallback `[ANSWER]` so the asker
    isn't blocked silently. The fallback names the tool calls used
    so QA can ask a more targeted follow-up.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.llm.client import MockTurn, ToolCall
from core.runner import (
    AgentConfig,
    AgentRunner,
    PEER_ANSWER_MAX_ITERATIONS,
    ProjectConfig,
)
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC
from core.workspace import CommitRecord


def _project_with_dev() -> ProjectConfig:
    return ProjectConfig(
        project_id="p_peer_answer",
        goal="ship a thing",
        agents=[AgentConfig(role="dev", description="codes")],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
        max_iterations=3,
    )


def _seed_dev_with_commit(runner: AgentRunner) -> None:
    """Pretend `dev` shipped some files and posted a COMMIT block this run.
    Lets the 'own_work' context layer have something to render."""
    runner.workspace.git.commits.append(CommitRecord(
        hash="abc123",
        branch="agent/dev",
        agent="dev",
        message="feat: ship auth",
        iteration=0,
        files=["src/auth.py", "tests/test_auth.py"],
    ))
    runner.workspace.post_signal(Signal(
        type=SignalType.PROTOCOL,
        summary="COMMIT dev",
        from_agent="dev",
        detail="COMMIT dev iteration 0\nsummary: auth endpoint done; "
               "POST /auth covers 400/401/500\nfiles: src/auth.py, tests/test_auth.py",
        iteration=0,
    ))


# ── Happy path: single-turn text answer ─────────────────────────────────────


@pytest.mark.asyncio
async def test_single_turn_text_answer_returned_directly(tmp_path: Path):
    """Model emits [ANSWER] text on its first turn — return as-is, no loop."""
    proj = _project_with_dev()
    answer_text = (
        "[ANSWER]\n  ask_id: a1\n  to: qa\n"
        "  answer: state lives in src/auth.py:Session"
    )
    llm = MockLLMClient([MockTurn(text=answer_text, stop_reason="end_turn")])
    runner = AgentRunner(
        project=proj, run_id="r_single",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    agent = runner.agents["dev"]

    out = await runner._generic_peer_answer(
        agent, asker_role="qa", ask_id="a1",
        question="how is state managed?", context_paths=[],
    )
    assert "[ANSWER]" in out
    assert "Session" in out
    assert len(llm.calls) == 1


# ── Tool-use loop: call tool, then answer ───────────────────────────────────


@pytest.mark.asyncio
async def test_tool_call_then_text_answer_completes_in_loop(tmp_path: Path):
    """Codestral failure mode in run_6b8d0190b2: model wants to fs-list
    before answering. Old code swallowed this and returned empty.
    New code executes the tool then loops; second turn emits [ANSWER]."""
    proj = _project_with_dev()
    llm = MockLLMClient([
        # Turn 1: model calls fs-list (no text)
        MockTurn(
            text="",
            tool_calls=[ToolCall(id="t1", name="fs-list", arguments={"path": "src"})],
            stop_reason="tool_use",
        ),
        # Turn 2: model emits the answer
        MockTurn(
            text="[ANSWER]\n  ask_id: a2\n  to: qa\n"
                 "  answer: src/auth.py is the main module",
            stop_reason="end_turn",
        ),
    ])
    runner = AgentRunner(
        project=proj, run_id="r_tooltext",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    agent = runner.agents["dev"]

    out = await runner._generic_peer_answer(
        agent, asker_role="qa", ask_id="a2",
        question="what files do you own?", context_paths=[],
    )
    assert "[ANSWER]" in out
    assert "src/auth.py" in out
    # Two LLM calls: tool call + answer
    assert len(llm.calls) == 2


# ── Cap exhaustion: fallback synthesis ──────────────────────────────────────


@pytest.mark.asyncio
async def test_cap_exhaustion_synthesizes_fallback_answer(tmp_path: Path):
    """Codestral that keeps calling tools and never answers must NOT
    produce an empty answer. The runtime synthesizes a fallback so QA
    sees something useful and can ask a more targeted follow-up."""
    proj = _project_with_dev()
    # Queue PEER_ANSWER_MAX_ITERATIONS+1 tool-only turns; the loop
    # must bail at the cap and synthesize.
    tool_turn = MockTurn(
        text="",
        tool_calls=[ToolCall(id="t", name="fs-list", arguments={"path": "."})],
        stop_reason="tool_use",
    )
    llm = MockLLMClient([tool_turn] * (PEER_ANSWER_MAX_ITERATIONS + 1))
    runner = AgentRunner(
        project=proj, run_id="r_exhaust",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    agent = runner.agents["dev"]

    out = await runner._generic_peer_answer(
        agent, asker_role="qa", ask_id="a3",
        question="what does the system do?", context_paths=[],
    )
    # Synthetic ANSWER is emitted — never empty.
    assert "[ANSWER]" in out
    assert "ask_id: a3" in out
    assert "to: qa" in out
    # The synthetic message names the tools used so QA can retarget.
    assert "fs-list" in out
    # Loop hit the cap.
    assert len(llm.calls) == PEER_ANSWER_MAX_ITERATIONS + 1


@pytest.mark.asyncio
async def test_no_tools_no_answer_returns_text_or_synthesizes(tmp_path: Path):
    """If the model emits text without [ANSWER] and no tool calls,
    return that text — the caller's protocol parser handles the failure."""
    proj = _project_with_dev()
    llm = MockLLMClient([MockTurn(
        text="Sorry, I don't know.",
        stop_reason="end_turn",
    )])
    runner = AgentRunner(
        project=proj, run_id="r_giveup",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    agent = runner.agents["dev"]

    out = await runner._generic_peer_answer(
        agent, asker_role="qa", ask_id="a4",
        question="anything?", context_paths=[],
    )
    # Returned the model's text verbatim; downstream extract_trailing
    # will fail to parse [ANSWER] and the dispatcher posts answer="".
    assert out == "Sorry, I don't know."


# ── Own-work context injection ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_responder_own_commits_injected_into_prompt(tmp_path: Path):
    """The responder gets their OWN recent commits + last COMMIT block in
    the prompt so they can answer from 'what I just shipped' without
    re-exploring. Diagnosed need from run_6b8d0190b2 where game_logic_dev
    had just committed level_state.py + 6 systems but reached for fs-list
    instead of answering directly."""
    proj = _project_with_dev()
    answer = "[ANSWER]\n  ask_id: a5\n  to: qa\n  answer: see auth.py"
    llm = MockLLMClient([MockTurn(text=answer, stop_reason="end_turn")])
    runner = AgentRunner(
        project=proj, run_id="r_ownwork",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    _seed_dev_with_commit(runner)
    agent = runner.agents["dev"]

    await runner._generic_peer_answer(
        agent, asker_role="qa", ask_id="a5",
        question="explain auth", context_paths=[],
    )
    # The first call's user message must include the own-work context.
    first_call_messages = llm.calls[0]["messages"]
    user_content = str(first_call_messages[0].content)
    assert "YOUR RECENT WORK" in user_content
    assert "src/auth.py" in user_content
    assert "tests/test_auth.py" in user_content
    # The COMMIT block summary should also be there.
    assert "auth endpoint done" in user_content


@pytest.mark.asyncio
async def test_responder_with_no_commits_omits_own_work_block(tmp_path: Path):
    """Quiet on missing data — when the responder hasn't committed
    anything yet, the own-work block is omitted entirely (not rendered
    as 'Files you committed: (none)' which adds noise)."""
    proj = _project_with_dev()
    answer = "[ANSWER]\n  ask_id: a6\n  to: qa\n  answer: nothing yet"
    llm = MockLLMClient([MockTurn(text=answer, stop_reason="end_turn")])
    runner = AgentRunner(
        project=proj, run_id="r_nowork",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    # NOTE: no commits seeded
    agent = runner.agents["dev"]

    await runner._generic_peer_answer(
        agent, asker_role="qa", ask_id="a6",
        question="what's done?", context_paths=[],
    )
    user_content = str(llm.calls[0]["messages"][0].content)
    assert "YOUR RECENT WORK" not in user_content


@pytest.mark.asyncio
async def test_context_paths_from_asker_appear_in_prompt(tmp_path: Path):
    """Files the asker cited (`ask-peer(context_paths=[...])`) get a
    clearly-labeled section so the responder can read them if needed."""
    proj = _project_with_dev()
    answer = "[ANSWER]\n  ask_id: a7\n  to: qa\n  answer: yes"
    llm = MockLLMClient([MockTurn(text=answer, stop_reason="end_turn")])
    runner = AgentRunner(
        project=proj, run_id="r_ctxpaths",
        run_root=tmp_path / "run", llm=llm,
    )
    runner._build_agents()
    agent = runner.agents["dev"]

    await runner._generic_peer_answer(
        agent, asker_role="qa", ask_id="a7",
        question="is it documented?",
        context_paths=["docs/auth.md", "docs/threats.md"],
    )
    user_content = str(llm.calls[0]["messages"][0].content)
    assert "Context files cited by asker" in user_content
    assert "docs/auth.md" in user_content
    assert "docs/threats.md" in user_content
