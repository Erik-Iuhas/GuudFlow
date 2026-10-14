"""Streaming calls: the tool-call arguments reach `on_text` as they arrive, and the response is
the same as a non-streamed call."""

from __future__ import annotations

import json

import httpx

from core.llm.client import LLMMessage
from core.llm.openai import OpenAIClient


def _sse(chunks: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


async def test_openai_compatible_stream_folds_into_one_response() -> None:
    args = json.dumps({"changes": [{"op": "add_item", "title": "One"}], "reply": "ok"})
    parts = [args[:20], args[20:45], args[45:]]
    chunks = [{"model": "m", "choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "submit_plan_turn", "arguments": ""}}]}}]}]
    chunks += [{"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": p}}]}}]}
               for p in parts]
    chunks += [{"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}]
    seen_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_body.update(json.loads(request.content))
        return httpx.Response(200, content=_sse(chunks),
                              headers={"content-type": "text/event-stream"})

    client = OpenAIClient(base_url="https://api.mistral.ai/v1", api_key="k",
                          http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    texts: list[str] = []
    resp = await client.complete_streaming("sys", [LLMMessage(role="user", content="hi")],
                                           tools=[{"name": "submit_plan_turn", "description": "d",
                                                   "input_schema": {"type": "object"}}],
                                           on_text=texts.append)
    assert seen_body["stream"] is True and "stream_options" not in seen_body
    assert texts[-1] == args and len(texts) == 3
    assert resp.tool_calls[0].name == "submit_plan_turn"
    assert resp.tool_calls[0].arguments["changes"][0]["title"] == "One"
    assert resp.input_tokens == 10 and resp.output_tokens == 5


async def test_anthropic_stream_passes_partial_json_and_returns_the_final_message() -> None:
    from types import SimpleNamespace

    from core.llm.anthropic import AnthropicClient

    final = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name="submit_plan_turn",
                                 input={"changes": [], "reply": "ok"})],
        stop_reason="tool_use", model="claude-x",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0))

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            async def gen():
                for part in ('{"changes": [', '], "reply": "ok"}'):
                    yield SimpleNamespace(type="input_json", partial_json=part)
            return gen()

        async def get_final_message(self):
            return final

    client = AnthropicClient(api_key="k")
    client._client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: Stream()))
    texts: list[str] = []
    resp = await client.complete_streaming("s", [LLMMessage(role="user", content="hi")],
                                           on_text=texts.append)
    assert texts == ['{"changes": [', '{"changes": [], "reply": "ok"}']
    assert resp.tool_calls[0].name == "submit_plan_turn" and resp.input_tokens == 3
