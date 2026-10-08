"""Tests for core/llm.py — the LLM abstraction + MockLLMClient."""

from __future__ import annotations

import pytest

from core.llm import (
    LLMMessage,
    LLMResponse,
    MockLLMClient,
    MockTurn,
    ToolCall,
)


async def test_mock_returns_text_turn():
    client = MockLLMClient([MockTurn(text="hello world")])
    res = await client.complete(system="s", messages=[LLMMessage("user", "hi")])
    assert res.text == "hello world"
    assert res.stop_reason == "end_turn"


async def test_mock_returns_tool_call():
    client = MockLLMClient([MockTurn(tool_calls=[
        ToolCall(id="t1", name="fs-read", arguments={"path": "x"}),
    ])])
    res = await client.complete(system="s", messages=[LLMMessage("user", "do thing")])
    assert res.stop_reason == "tool_use"
    assert res.tool_calls[0].name == "fs-read"


async def test_mock_records_calls():
    client = MockLLMClient([MockTurn(text="ok")])
    await client.complete(system="s", messages=[LLMMessage("user", "hi")], max_tokens=100)
    assert len(client.calls) == 1
    assert client.calls[0]["max_tokens"] == 100


async def test_mock_exhausted_raises():
    client = MockLLMClient([MockTurn(text="one")])
    await client.complete(system="s", messages=[LLMMessage("user", "x")])
    with pytest.raises(AssertionError, match="exhausted"):
        await client.complete(system="s", messages=[LLMMessage("user", "x")])


async def test_mock_queue_extends_script():
    client = MockLLMClient([MockTurn(text="first")])
    client.queue(MockTurn(text="second"), MockTurn(text="third"))
    out = []
    for _ in range(3):
        r = await client.complete(system="s", messages=[])
        out.append(r.text)
    assert out == ["first", "second", "third"]


async def test_mock_explicit_stop_reason():
    client = MockLLMClient([MockTurn(text="", stop_reason="max_tokens")])
    res = await client.complete(system="s", messages=[])
    assert res.stop_reason == "max_tokens"


async def test_anthropic_client_sends_effort_in_output_config() -> None:
    from types import SimpleNamespace

    from core.llm.anthropic import AnthropicClient
    sent: list[dict] = []

    class Messages:
        async def create(self, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")],
                                   stop_reason="end_turn", model="m",
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                                         cache_read_input_tokens=0,
                                                         cache_creation_input_tokens=0))

    client = AnthropicClient(api_key="k")
    client._client = SimpleNamespace(messages=Messages())
    from core.llm import LLMMessage
    await client.complete(system="", messages=[LLMMessage(role="user", content="hi")], effort="high")
    await client.complete(system="", messages=[LLMMessage(role="user", content="hi")])
    assert sent[0]["extra_body"] == {"output_config": {"effort": "high"}}
    assert "extra_body" not in sent[1]
