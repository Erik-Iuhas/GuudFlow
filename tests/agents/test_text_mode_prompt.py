"""Tests for the text-mode branch in BaseAgent's prompt builder.

When `_tool_format == "text"`:
  - llm() must NOT pass tools=[...] to the LLM client (we observe via mock)
  - effective_system_prompt() must inject the text-format primer (Layer 6.4)
  - the primer must group tools into action vs context sections (Phase 5)

When `_tool_format == "structured"` (default):
  - llm() passes the structured tool_calls schema as before
  - the primer is NOT injected
"""
from __future__ import annotations

import pytest

from agents.base import BaseAgent
from core.llm import LLMMessage, LLMResponse
from core.tool_manifest import ToolDescriptor, ToolManifest


class _RecordingLLM:
    """Capture every complete() call so tests can assert on tool routing."""

    def __init__(self, response: LLMResponse | None = None):
        self.response = response or LLMResponse(text="ok", stop_reason="end_turn")
        self.calls: list[dict] = []

    async def complete(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        return self.response


def _make_agent(tool_format: str) -> tuple[BaseAgent, _RecordingLLM]:
    agent = BaseAgent(role="dev", description="unit-test agent")
    agent._tool_format = tool_format
    agent._manifest = ToolManifest(descriptors=[
        ToolDescriptor(
            name="fs-write",
            description="Write a file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            invocation="local",
        ),
        ToolDescriptor(
            name="fs-read",
            description="Read a file",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            invocation="local",
        ),
    ])
    llm = _RecordingLLM()
    agent._llm = llm
    agent._model = "test-model"
    return agent, llm


@pytest.mark.asyncio
async def test_structured_mode_passes_tools_to_llm() -> None:
    """Default (structured) mode: tools=[...] reaches the LLM client."""
    agent, llm = _make_agent("structured")
    await agent.llm([LLMMessage(role="user", content="hi")])
    assert len(llm.calls) == 1
    tools = llm.calls[0]["tools"]
    assert tools is not None
    assert {t["name"] for t in tools} == {"fs-write", "fs-read"}


@pytest.mark.asyncio
async def test_text_mode_passes_no_tools_to_llm() -> None:
    """Text mode: tools=None — the LLM never sees a structured schema, so
    it can't emit (and can't fail at) structured tool_calls."""
    agent, llm = _make_agent("text")
    await agent.llm([LLMMessage(role="user", content="hi")])
    assert len(llm.calls) == 1
    assert llm.calls[0]["tools"] is None


@pytest.mark.asyncio
async def test_text_mode_injects_primer_in_system_prompt() -> None:
    """The text-format primer (Layer 6.4) appears in effective_system_prompt
    when in text mode, with both action and context tool sections."""
    agent, llm = _make_agent("text")
    await agent.llm([LLMMessage(role="user", content="hi")])
    system = llm.calls[0]["system"]
    assert "TOOL CALLING — TEXT FORMAT" in system
    assert "[TOOL <tool-name>]" in system
    assert "[/TOOL]" in system
    # Phase 5 — split sections. Other layers (autonomy rules, dispatch
    # rule) mention tool names too, so we slice from the primer header
    # to assert positions only inside the catalog itself.
    assert "Action tools" in system
    assert "Context tools" in system
    primer_start = system.index("Action tools")
    primer = system[primer_start:]
    action_idx = primer.index("Action tools")
    context_idx = primer.index("Context tools")
    fs_write_idx = primer.index("fs-write")
    fs_read_idx = primer.index("fs-read")
    assert action_idx < fs_write_idx < context_idx, (
        "fs-write must be listed under Action tools, before Context tools heading"
    )
    assert context_idx < fs_read_idx, (
        "fs-read must be listed under Context tools"
    )


@pytest.mark.asyncio
async def test_structured_mode_does_not_inject_text_primer() -> None:
    """Structured mode keeps the existing prompt shape unchanged — no
    text-format primer leaks into prompts that already use tool_calls."""
    agent, llm = _make_agent("structured")
    await agent.llm([LLMMessage(role="user", content="hi")])
    system = llm.calls[0]["system"]
    assert "TOOL CALLING — TEXT FORMAT" not in system


@pytest.mark.asyncio
async def test_empty_manifest_yields_empty_primer() -> None:
    """An agent with no tools shouldn't emit a primer at all — the
    'Available tools' catalog would be empty."""
    agent, llm = _make_agent("text")
    agent._manifest = ToolManifest(descriptors=[])
    primer = agent._text_tool_format_primer()
    assert primer == ""


def test_primer_signature_renders_required_vs_optional() -> None:
    """`?` suffix on optional args, no marker on required — gives the model
    a fast schema cue without dumping the full JSON Schema in the prompt."""
    agent = BaseAgent(role="t", description="")
    agent._tool_format = "text"
    agent._manifest = ToolManifest(descriptors=[
        ToolDescriptor(
            name="opt-test",
            description="optional args sample",
            input_schema={
                "type": "object",
                "properties": {
                    "must": {"type": "string"},
                    "maybe": {"type": "integer"},
                },
                "required": ["must"],
            },
            invocation="local",
        ),
    ])
    primer = agent._text_tool_format_primer()
    assert "must:string" in primer
    assert "maybe?:integer" in primer
