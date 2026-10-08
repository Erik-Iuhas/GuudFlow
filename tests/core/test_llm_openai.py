"""Tests for the OpenAI-compatible adapter (covers Ollama, LM Studio, etc.).

Uses `httpx.MockTransport` so no real network calls are made — same
pattern any OpenAI-compatible server would respond with.
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.llm import (
    AuthError,
    LLMMessage,
    OpenAIClient,
    RateLimitError,
    ToolCall,
    _from_openai_response,
    _to_openai_messages,
    _to_openai_tool,
    ollama,
    openai_compatible,
)


# ── Translation: our format → OpenAI ───────────────────────────────────────


def test_to_openai_messages_string_content():
    out = _to_openai_messages(LLMMessage(role="user", content="hello"))
    assert out == [{"role": "user", "content": "hello"}]


def test_to_openai_messages_assistant_text_only():
    msg = LLMMessage(role="assistant", content=[{"type": "text", "text": "ok"}])
    out = _to_openai_messages(msg)
    assert len(out) == 1
    assert out[0]["role"] == "assistant"
    assert out[0]["content"] == "ok"
    assert "tool_calls" not in out[0]


def test_to_openai_messages_assistant_tool_use():
    msg = LLMMessage(role="assistant", content=[
        {"type": "text", "text": "calling fs:read"},
        {"type": "tool_use", "id": "tu_1", "name": "fs-read", "input": {"path": "x.py"}},
    ])
    out = _to_openai_messages(msg)
    assert len(out) == 1
    asst = out[0]
    assert asst["role"] == "assistant"
    assert asst["content"] == "calling fs:read"
    assert asst["tool_calls"][0]["id"] == "tu_1"
    assert asst["tool_calls"][0]["function"]["name"] == "fs-read"
    # arguments is a JSON STRING per OpenAI spec
    args = json.loads(asst["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "x.py"}


def test_to_openai_messages_assistant_no_text_with_tools():
    """When the LLM only emitted tool_use blocks, content must be null
    (OpenAI spec) and tool_calls must be present."""
    msg = LLMMessage(role="assistant", content=[
        {"type": "tool_use", "id": "tu_1", "name": "fs-read", "input": {}},
    ])
    out = _to_openai_messages(msg)
    assert out[0]["content"] is None
    assert out[0]["tool_calls"]


def test_to_openai_messages_tool_results_become_tool_messages():
    """Each Anthropic-style tool_result block becomes its own role=tool
    message in OpenAI's format."""
    msg = LLMMessage(role="user", content=[
        {"type": "tool_result", "tool_use_id": "tu_1", "content": '{"ok": true}'},
        {"type": "tool_result", "tool_use_id": "tu_2", "content": '{"err": "x"}'},
    ])
    out = _to_openai_messages(msg)
    assert len(out) == 2
    assert all(m["role"] == "tool" for m in out)
    assert out[0]["tool_call_id"] == "tu_1"
    assert out[1]["tool_call_id"] == "tu_2"
    assert "ok" in out[0]["content"]


def test_to_openai_messages_user_text_with_tool_results():
    """User message with text + tool_result blocks → user text msg first,
    then tool messages."""
    msg = LLMMessage(role="user", content=[
        {"type": "text", "text": "follow-up"},
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "result"},
    ])
    out = _to_openai_messages(msg)
    assert len(out) == 2
    assert out[0]["role"] == "user"
    assert out[1]["role"] == "tool"


def test_to_openai_tool_schema():
    anthropic_tool = {
        "name": "fs-read",
        "description": "Read a file",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
    out = _to_openai_tool(anthropic_tool)
    assert out["type"] == "function"
    assert out["function"]["name"] == "fs-read"
    assert out["function"]["parameters"]["properties"]["path"]["type"] == "string"


def test_to_openai_tool_default_parameters():
    out = _to_openai_tool({"name": "noop", "description": ""})
    assert out["function"]["parameters"] == {"type": "object", "properties": {}}


# ── Translation: OpenAI → our format ───────────────────────────────────────


def test_from_openai_response_text_only():
    resp = {
        "model": "gpt-4o-mini",
        "choices": [{"message": {"role": "assistant", "content": "hello world"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    out = _from_openai_response(resp)
    assert out.text == "hello world"
    assert out.stop_reason == "end_turn"
    assert out.tool_calls == []
    assert out.model == "gpt-4o-mini"
    assert out.input_tokens == 5
    assert out.output_tokens == 3


def test_from_openai_response_tool_calls():
    resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "fs-read",
                        "arguments": json.dumps({"path": "auth.py"}),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }
    out = _from_openai_response(resp)
    assert out.stop_reason == "tool_use"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "fs-read"
    assert out.tool_calls[0].arguments == {"path": "auth.py"}


def test_from_openai_response_handles_malformed_arguments():
    resp = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "id": "call_x",
                    "function": {"name": "x", "arguments": "{invalid json"},
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }
    out = _from_openai_response(resp)
    assert out.tool_calls[0].arguments == {}


def test_from_openai_response_finish_reason_mapping():
    cases = [("stop", "end_turn"),
             ("tool_calls", "tool_use"),
             ("function_call", "tool_use"),
             ("length", "max_tokens"),
             ("content_filter", "error")]
    for openai_reason, expected in cases:
        resp = {"choices": [{"message": {"content": ""}, "finish_reason": openai_reason}]}
        assert _from_openai_response(resp).stop_reason == expected


def test_from_openai_response_empty_choices():
    out = _from_openai_response({"choices": []})
    assert out.text == ""
    assert out.tool_calls == []


# ── Defensive recovery: text-as-tool-call (Codestral failure mode) ───────


def test_salvages_text_as_tool_call_when_no_structured_tool_calls():
    """Codestral / some Mistral models occasionally write a tool call
    as plain text content with finish_reason="stop". Without recovery
    the runner sees end_turn → no tool calls → synthesises a no-op
    completion. With recovery we extract the call and re-emit as
    tool_use. Diagnosed in run_2b1c86d56f for renderer_dev."""
    resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": 'fs-list {"path": "src/render", "recursive": true}',
            },
            "finish_reason": "stop",
        }],
    }
    out = _from_openai_response(resp)
    assert out.stop_reason == "tool_use"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "fs-list"
    assert out.tool_calls[0].arguments == {"path": "src/render", "recursive": True}
    # Salvaged calls get a recognisable id prefix for log debugging.
    assert out.tool_calls[0].id.startswith("salvaged_")
    # Text is consumed — it's now in the synthetic tool_use, not free text.
    assert out.text == ""


def test_does_not_salvage_when_structured_tool_calls_already_present():
    """If the model emitted real tool_calls, never second-guess them
    even if the content also has tool-call-shaped text."""
    resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": 'fs-list {"path": "x"}',
                "tool_calls": [{
                    "id": "real_call",
                    "function": {"name": "fs-read", "arguments": '{"path": "real.py"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }
    out = _from_openai_response(resp)
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "fs-read"
    assert out.tool_calls[0].id == "real_call"


def test_does_not_salvage_normal_prose_that_mentions_a_tool():
    """English text shouldn't trigger recovery just because it mentions
    a tool name. The whole-string regex prevents this."""
    cases = [
        "I'll use fs-list to check the directory.",
        "Calling fs-read on auth.py to inspect.",
        "fs-list with recursive=true would be ideal.",
    ]
    for text in cases:
        resp = {
            "choices": [{
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
        }
        out = _from_openai_response(resp)
        assert out.stop_reason == "end_turn"
        assert out.tool_calls == []
        assert out.text == text


def test_does_not_salvage_when_finish_reason_is_length():
    """Truncation has its own recovery path (continuation prompt). Don't
    misinterpret a truncated `tool-name {partial...` as a real tool call."""
    resp = {
        "choices": [{
            "message": {"role": "assistant", "content": 'fs-list {"path": "x"}'},
            "finish_reason": "length",
        }],
    }
    out = _from_openai_response(resp)
    # Recovery only fires for end_turn; max_tokens passes through.
    assert out.stop_reason == "max_tokens"
    assert out.tool_calls == []


def test_does_not_salvage_invalid_json_args():
    """Garbled JSON args shouldn't synthesise a tool call — no execution
    is better than wrong execution."""
    resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": 'fs-list {invalid json here',
            },
            "finish_reason": "stop",
        }],
    }
    out = _from_openai_response(resp)
    assert out.stop_reason == "end_turn"
    assert out.tool_calls == []


def test_salvage_handles_hyphenated_and_underscored_tool_names():
    """Tool names use hyphens (fs-list, ask-peer) per the project's
    naming convention. Underscores covered for legacy/MCP names."""
    for name in ("fs-list", "ask-peer", "code_outline", "mcp-github-list"):
        resp = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": f'{name} {{"x": 1}}',
                },
                "finish_reason": "stop",
            }],
        }
        out = _from_openai_response(resp)
        assert out.tool_calls and out.tool_calls[0].name == name, name


# ── End-to-end with httpx.MockTransport ────────────────────────────────────


def _make_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_complete_sends_correct_request():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={
            "model": "gpt-4o-mini",
            "choices": [{"message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    transport = _make_transport(handler)
    client = OpenAIClient(
        base_url="http://example.test/v1",
        api_key="sk-test",
        http_client=transport,
    )
    res = await client.complete(
        system="be helpful",
        messages=[LLMMessage(role="user", content="hi")],
        max_tokens=128,
        model="gpt-4o-mini",
    )
    assert res.text == "ok"
    assert captured["url"].endswith("/chat/completions")
    body = captured["body"]
    assert body["model"] == "gpt-4o-mini"
    assert body["max_tokens"] == 128
    # System message comes first.
    assert body["messages"][0] == {"role": "system", "content": "be helpful"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    assert captured["headers"]["authorization"] == "Bearer sk-test"


async def test_complete_includes_tools_when_provided():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })

    transport = _make_transport(handler)
    client = OpenAIClient(base_url="http://x/v1", api_key="k", http_client=transport)
    await client.complete(
        system="s",
        messages=[LLMMessage(role="user", content="x")],
        tools=[{"name": "fs-read", "description": "Read", "input_schema": {"type": "object"}}],
    )
    assert "tools" in captured["body"]
    assert captured["body"]["tools"][0]["function"]["name"] == "fs-read"


async def test_complete_no_api_key_omits_authorization():
    """Local Ollama doesn't need (or care about) the Authorization header."""
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })

    transport = _make_transport(handler)
    client = OpenAIClient(base_url="http://localhost:11434/v1", api_key=None, http_client=transport)
    await client.complete(system="s", messages=[LLMMessage(role="user", content="x")])
    assert "authorization" not in captured["headers"]


async def test_complete_extra_headers_passed_through():
    """OpenRouter requires HTTP-Referer; verify extra_headers reach the wire."""
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })

    transport = _make_transport(handler)
    client = OpenAIClient(
        base_url="http://x/v1", api_key="k",
        http_client=transport,
        extra_headers={"http-referer": "https://guudflow.dev"},
    )
    await client.complete(system="s", messages=[LLMMessage(role="user", content="x")])
    assert captured["headers"]["http-referer"] == "https://guudflow.dev"


async def test_complete_round_trips_tool_use():
    """Full round-trip: send a tool call request, get back tool_use response,
    verify it's translated into our LLMResponse format."""
    def handler(request):
        return httpx.Response(200, json={
            "model": "qwen2.5:7b",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "tu_1",
                        "type": "function",
                        "function": {
                            "name": "fs-read",
                            "arguments": json.dumps({"path": "x.py"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        })

    transport = _make_transport(handler)
    client = OpenAIClient(base_url="http://localhost:11434/v1", http_client=transport)
    res = await client.complete(
        system="be helpful",
        messages=[LLMMessage(role="user", content="read x.py")],
        tools=[{"name": "fs-read", "description": "Read a file",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}],
    )
    assert res.stop_reason == "tool_use"
    assert res.tool_calls[0].name == "fs-read"
    assert res.tool_calls[0].arguments == {"path": "x.py"}
    assert res.model == "qwen2.5:7b"


async def test_complete_raises_auth_error_on_401():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "unauthorized"}})

    transport = _make_transport(handler)
    client = OpenAIClient(base_url="http://x/v1", api_key="bad", http_client=transport)
    with pytest.raises(AuthError):
        await client.complete(system="s", messages=[LLMMessage(role="user", content="x")])


async def test_complete_raises_rate_limit_error_on_429():
    def handler(request):
        resp = httpx.Response(429, json={"error": {"message": "rate limited"}})
        return resp

    transport = _make_transport(handler)
    client = OpenAIClient(base_url="http://x/v1", api_key="k", http_client=transport)
    with pytest.raises(RateLimitError):
        await client.complete(system="s", messages=[LLMMessage(role="user", content="x")])


# ── Factories ──────────────────────────────────────────────────────────────


def test_ollama_factory_defaults():
    client = ollama()
    assert client._base_url == "http://localhost:11434/v1"
    assert client._api_key == "ollama"


def test_ollama_factory_custom_url():
    client = ollama(base_url="http://gpu-box:11434/v1")
    assert client._base_url == "http://gpu-box:11434/v1"


def test_openai_compatible_factory():
    client = openai_compatible(
        "http://localhost:1234/v1", "lm-studio",
        extra_headers={"X-Source": "guudflow"},
    )
    assert client._base_url == "http://localhost:1234/v1"
    assert client._api_key == "lm-studio"
    assert client._extra_headers == {"X-Source": "guudflow"}


async def test_effort_is_sent_as_reasoning_effort() -> None:
    import httpx

    from core.llm.openai import OpenAIClient
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"},
                                                      "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = OpenAIClient(base_url="https://x/v1", api_key="k", http_client=http)
        await client.complete(system="", messages=[LLMMessage(role="user", content="hi")],
                              effort="none")
        await client.complete(system="", messages=[LLMMessage(role="user", content="hi")])
    assert seen[0]["reasoning_effort"] == "none" and "reasoning_effort" not in seen[1]
