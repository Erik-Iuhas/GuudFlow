"""Gateway tests. Upstreams are httpx.MockTransport; the e2e test uses a real FakeLLM server."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.harness.mcp_server import serve_app
from gateway.app import Gateway, Price, Upstream
from tests.harness.fake_llm import FakeLLM

REAL_ANTHROPIC = "sk-ant-REAL-secret"
REAL_OPENAI = "sk-oai-REAL-secret"
CLAUDE = "claude-test"
GPT = "gpt-test"
PRICES = {
    CLAUDE: Price(input_per_1k=0.003, output_per_1k=0.015, cache_read_per_1k=0.0003),
    GPT: Price(input_per_1k=0.001, output_per_1k=0.002, cache_read_per_1k=0.0001),
    "fake-model": Price(input_per_1k=0.01, output_per_1k=0.02),
}
UPSTREAMS = {
    "anthropic": Upstream("anthropic", "anthropic", "https://api.anthropic.test/v1", REAL_ANTHROPIC),
    "openai": Upstream("openai", "openai_compatible", "https://api.openai.test/v1", REAL_OPENAI),
}


def _sse(*events: tuple[str | None, dict[str, Any] | str]) -> bytes:
    out = ""
    for name, data in events:
        if name:
            out += f"event: {name}\n"
        out += f"data: {data if isinstance(data, str) else json.dumps(data)}\n\n"
    return out.encode()


ANTHROPIC_SSE = _sse(
    ("message_start", {"type": "message_start", "message": {"id": "m", "usage": {
        "input_tokens": 1000, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 0,
        "output_tokens": 1}}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "hi"}}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                       "usage": {"output_tokens": 250}}),
    ("message_delta", {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 500}}),
    ("message_stop", {"type": "message_stop"}),
)
OPENAI_SSE = _sse(
    (None, {"choices": [{"index": 0, "delta": {"content": "hi"}}], "usage": None}),
    (None, {"choices": [], "usage": {"prompt_tokens": 3000, "completion_tokens": 400,
                                     "prompt_tokens_details": {"cached_tokens": 1000}}}),
    (None, "[DONE]"),
)


async def _chunked(data: bytes, size: int = 7) -> AsyncIterator[bytes]:
    """Split the body at arbitrary points, including mid-line."""
    for i in range(0, len(data), size):
        yield data[i:i + size]


class Upstreams:
    """MockTransport handler: records requests and serves a configurable canned response."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responder: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, json={"ok": True})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request)

    def respond(self, response: httpx.Response) -> None:
        self.responder = lambda _r: response


@pytest.fixture
def upstream() -> Upstreams:
    return Upstreams()


@pytest.fixture
async def gateway(upstream: Upstreams, tmp_path: Path) -> AsyncIterator[Gateway]:
    gw = Gateway(UPSTREAMS, PRICES.get, cost_log=tmp_path / "cost.jsonl",
                 transport=httpx.MockTransport(upstream))
    yield gw
    await gw.aclose()


@pytest.fixture
async def client(gateway: Gateway) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app()),
                                 base_url="http://gateway") as c:
        yield c


def _anthropic_body(model: str = CLAUDE, **extra: Any) -> dict[str, Any]:
    return {"model": model, "max_tokens": 10, "stream": True,
            "messages": [{"role": "user", "content": "hello"}], **extra}


def _anthropic_headers(key: str) -> dict[str, str]:
    return {"x-api-key": key, "anthropic-version": "2023-06-01", "anthropic-beta": "tools-2024"}


# ── auth, routing, allow-list ───────────────────────────────────────────────
async def test_bad_key_is_401(client: httpx.AsyncClient, upstream: Upstreams) -> None:
    r = await client.post("/anthropic/messages", json=_anthropic_body(),
                          headers=_anthropic_headers("nope"))
    assert r.status_code == 401
    assert r.json()["error"]["type"] == "authentication_error"
    r = await client.post("/anthropic/messages", json=_anthropic_body())
    assert r.status_code == 401
    assert upstream.requests == []


async def test_model_not_allowed_is_403(client: httpx.AsyncClient, gateway: Gateway,
                                        upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    r = await client.post("/anthropic/messages", json=_anthropic_body("claude-opus-secret"),
                          headers=_anthropic_headers(key))
    assert r.status_code == 403
    assert "claude-opus-secret" in r.json()["error"]["message"]
    empty = gateway.issue_key("run1", "nobody", [])
    r = await client.post("/anthropic/messages", json=_anthropic_body(),
                          headers=_anthropic_headers(empty))
    assert r.status_code == 403
    assert upstream.requests == []


async def test_unknown_upstream_is_404(client: httpx.AsyncClient, gateway: Gateway,
                                       upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    r = await client.post("/mistral/messages", json=_anthropic_body(),
                          headers=_anthropic_headers(key))
    assert r.status_code == 404
    assert upstream.requests == []


# ── credential swap ─────────────────────────────────────────────────────────
async def test_real_key_replaces_virtual_key_anthropic(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    upstream.respond(httpx.Response(200, content=ANTHROPIC_SSE,
                                    headers={"content-type": "text/event-stream"}))
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    r = await client.post("/anthropic/messages?beta=true", json=_anthropic_body(),
                          headers={**_anthropic_headers(key), "authorization": f"Bearer {key}"})
    assert r.status_code == 200
    (seen,) = upstream.requests
    assert str(seen.url) == "https://api.anthropic.test/v1/messages?beta=true"
    assert seen.headers["x-api-key"] == REAL_ANTHROPIC
    assert seen.headers["anthropic-version"] == "2023-06-01"
    assert seen.headers["anthropic-beta"] == "tools-2024"
    assert "authorization" not in seen.headers
    assert key not in repr(seen.headers.items()) and key.encode() not in seen.content


async def test_a_keyless_local_provider_gets_no_auth_header(upstream: Upstreams,
                                                            tmp_path: Path) -> None:
    """Ollama / LM Studio need no key: send no auth header (an empty "Bearer " fails)."""
    local = {"ollama": Upstream("ollama", "openai_compatible", "http://127.0.0.1:11434/v1", "")}
    gw = Gateway(local, PRICES.get, cost_log=tmp_path / "cost.jsonl",
                 transport=httpx.MockTransport(upstream))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app()),
                                     base_url="http://gateway") as c:
            key = gw.issue_key("run1", "dev", [GPT])
            r = await c.post("/ollama/chat/completions", headers={"authorization": f"Bearer {key}"},
                             json={"model": GPT, "messages": []})
        assert r.status_code == 200
        (seen,) = upstream.requests
        assert "authorization" not in seen.headers and "x-api-key" not in seen.headers
        assert key not in repr(seen.headers.items())   # the virtual key never leaves the gateway
    finally:
        await gw.aclose()


async def test_real_key_replaces_virtual_key_openai(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [GPT])
    r = await client.post("/openai/chat/completions", headers={"authorization": f"Bearer {key}"},
                          json={"model": GPT, "messages": []})
    assert r.status_code == 200
    (seen,) = upstream.requests
    assert str(seen.url) == "https://api.openai.test/v1/chat/completions"
    assert seen.headers["authorization"] == f"Bearer {REAL_OPENAI}"
    assert "x-api-key" not in seen.headers
    assert key not in repr(seen.headers.items())


async def test_only_inference_endpoints_are_reachable(client: httpx.AsyncClient,
                                                      gateway: Gateway,
                                                      upstream: Upstreams) -> None:
    """O8 review: the host's real key may only be used for model inference."""
    key = gateway.issue_key("run1", "dev", [GPT])
    auth = {"authorization": f"Bearer {key}"}
    assert (await client.get("/openai/models", headers=auth)).status_code == 403
    r = await client.post("/openai/files", headers=auth, json={"model": GPT})
    assert r.status_code == 403
    r = await client.post("/openai/chat/completions", headers=auth, content=b"not json")
    assert r.status_code == 400
    r = await client.post("/openai/chat/completions", headers=auth, json={"messages": []})
    assert r.status_code == 400
    assert not upstream.requests


# ── usage parsing and cost ──────────────────────────────────────────────────
async def test_anthropic_streaming_usage_and_cost(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    upstream.respond(httpx.Response(200, content=_chunked(ANTHROPIC_SSE),
                                    headers={"content-type": "text/event-stream"}))
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    r = await client.post("/anthropic/messages", json=_anthropic_body(),
                          headers=_anthropic_headers(key))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.content == ANTHROPIC_SSE  # body passes through unchanged
    (rec,) = gateway.usage("run1")
    assert (rec.input_tokens, rec.output_tokens, rec.cache_read_tokens) == (1000, 500, 2000)
    assert (rec.run_id, rec.role, rec.upstream, rec.model, rec.status) == (
        "run1", "dev", "anthropic", CLAUDE, 200)
    expected = 1000 * 0.003 / 1000 + 500 * 0.015 / 1000 + 2000 * 0.0003 / 1000
    assert rec.cost_usd == pytest.approx(expected)
    assert gateway.spend("run1") == pytest.approx(expected)
    assert gateway.spend("run1", "dev") == pytest.approx(expected)
    assert gateway.spend("run1", "other") == 0.0


async def test_openai_streaming_usage(client: httpx.AsyncClient, gateway: Gateway,
                                      upstream: Upstreams) -> None:
    upstream.respond(httpx.Response(200, content=_chunked(OPENAI_SSE, 5),
                                    headers={"content-type": "text/event-stream"}))
    key = gateway.issue_key("run1", "dev", [GPT])
    r = await client.post("/openai/chat/completions", headers={"authorization": f"Bearer {key}"},
                          json={"model": GPT, "stream": True, "messages": []})
    assert r.content == OPENAI_SSE
    (rec,) = gateway.usage("run1")
    # prompt_tokens includes the 1000 cached ones
    assert (rec.input_tokens, rec.output_tokens, rec.cache_read_tokens) == (2000, 400, 1000)
    assert rec.cost_usd == pytest.approx(2000 * 0.001 / 1000 + 400 * 0.002 / 1000
                                         + 1000 * 0.0001 / 1000)
    # include_usage was injected so the provider actually reports usage
    sent = json.loads(upstream.requests[0].content)
    assert sent["stream_options"] == {"include_usage": True}


async def test_non_stream_json_usage(client: httpx.AsyncClient, gateway: Gateway,
                                     upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE, GPT])
    upstream.respond(httpx.Response(200, json={"type": "message", "content": [], "usage": {
        "input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 50}}))
    r = await client.post("/anthropic/messages", json=_anthropic_body(stream=False),
                          headers=_anthropic_headers(key))
    assert r.json()["usage"]["input_tokens"] == 100
    upstream.respond(httpx.Response(200, json={"choices": [], "usage": {
        "prompt_tokens": 70, "completion_tokens": 30}}))
    await client.post("/openai/chat/completions", headers={"authorization": f"Bearer {key}"},
                      json={"model": GPT, "messages": []})
    first, second = gateway.usage("run1")
    assert (first.input_tokens, first.output_tokens, first.cache_read_tokens) == (100, 20, 50)
    assert (second.input_tokens, second.output_tokens, second.cache_read_tokens) == (70, 30, 0)


async def test_cache_writes_cost_their_own_price(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    """Anthropic bills cache writes at a premium: 1.25x input unless the model sets a price."""
    body = json.dumps({"id": "m", "type": "message", "content": [], "usage": {
        "input_tokens": 100, "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0,
        "output_tokens": 10}})
    upstream.respond(httpx.Response(200, content=body, headers={"content-type": "application/json"}))
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    await client.post("/anthropic/messages", json=_anthropic_body(stream=False),
                      headers=_anthropic_headers(key))
    (rec,) = gateway.usage("run1")
    assert rec.input_tokens == 4100  # the record still counts every input token
    expected = (100 * 0.003 + 4000 * 0.003 * 1.25 + 10 * 0.015) / 1000
    assert rec.cost_usd == pytest.approx(expected)


async def test_unknown_model_price_is_zero_but_logged(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", ["mystery"])
    upstream.respond(httpx.Response(200, json={"usage": {"prompt_tokens": 5, "completion_tokens": 5}}))
    await client.post("/openai/chat/completions", headers={"authorization": f"Bearer {key}"},
                      json={"model": "mystery", "messages": []})
    (rec,) = gateway.usage("run1")
    assert rec.cost_usd == 0.0 and rec.input_tokens == 5 and rec.model == "mystery"


async def test_images_counted(client: httpx.AsyncClient, gateway: Gateway,
                              upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE, GPT])
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA"}}
    tool_result = {"type": "tool_result", "tool_use_id": "t", "content": [img]}
    await client.post("/anthropic/messages", headers=_anthropic_headers(key), json={
        "model": CLAUDE, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "x"}, img, img, tool_result]}]})
    await client.post("/openai/chat/completions", headers={"authorization": f"Bearer {key}"}, json={
        "model": GPT, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "x"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]})
    anthropic_rec, openai_rec = gateway.usage("run1")
    assert anthropic_rec.images == 3
    assert openai_rec.images == 1


# ── budgets, revocation, logging ────────────────────────────────────────────
async def _spend_once(client: httpx.AsyncClient, upstream: Upstreams, key: str) -> httpx.Response:
    upstream.respond(httpx.Response(200, content=ANTHROPIC_SSE,
                                    headers={"content-type": "text/event-stream"}))
    return await client.post("/anthropic/messages", json=_anthropic_body(),
                             headers=_anthropic_headers(key))


async def test_key_budget_returns_402_without_upstream_call(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE], budget_usd=0.01)  # one call costs 0.0111
    assert (await _spend_once(client, upstream, key)).status_code == 200
    assert len(upstream.requests) == 1
    r = await _spend_once(client, upstream, key)
    assert r.status_code == 402
    assert r.json()["error"]["type"] == "budget_exceeded"
    assert len(upstream.requests) == 1


async def test_key_budget_not_hit_below_cap(client: httpx.AsyncClient, gateway: Gateway,
                                            upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE], budget_usd=0.02)
    assert (await _spend_once(client, upstream, key)).status_code == 200
    assert (await _spend_once(client, upstream, key)).status_code == 200  # 0.0111 < 0.02
    assert (await _spend_once(client, upstream, key)).status_code == 402  # 0.0222 >= 0.02


async def test_run_budget_spans_roles(client: httpx.AsyncClient, gateway: Gateway,
                                      upstream: Upstreams) -> None:
    dev = gateway.issue_key("run1", "dev", [CLAUDE])
    qa = gateway.issue_key("run1", "qa", [CLAUDE])
    elsewhere = gateway.issue_key("run2", "dev", [CLAUDE])
    gateway.set_run_budget("run1", 0.015)
    assert (await _spend_once(client, upstream, dev)).status_code == 200   # 0.0111
    assert (await _spend_once(client, upstream, qa)).status_code == 200    # still < 0.015
    assert (await _spend_once(client, upstream, dev)).status_code == 402   # 0.0222 total
    assert (await _spend_once(client, upstream, qa)).status_code == 402
    assert (await _spend_once(client, upstream, elsewhere)).status_code == 200  # other run
    assert gateway.spend("run1") == pytest.approx(0.0222)
    assert gateway.spend("run1", "qa") == pytest.approx(0.0111)
    gateway.set_run_budget("run1", None)
    assert (await _spend_once(client, upstream, dev)).status_code == 200


async def test_initial_run_budget_from_constructor(upstream: Upstreams) -> None:
    gw = Gateway(UPSTREAMS, PRICES.get, transport=httpx.MockTransport(upstream),
                 run_budget_usd={"run1": 0.0})
    key = gw.issue_key("run1", "dev", [CLAUDE])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app()),
                                 base_url="http://gateway") as c:
        r = await c.post("/anthropic/messages", json=_anthropic_body(),
                         headers=_anthropic_headers(key))
    assert r.status_code == 402 and upstream.requests == []
    await gw.aclose()


async def test_revoke_run_returns_401_and_keeps_usage(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    other = gateway.issue_key("run2", "dev", [CLAUDE])
    assert (await _spend_once(client, upstream, key)).status_code == 200
    gateway.revoke_run("run1")
    assert (await _spend_once(client, upstream, key)).status_code == 401
    assert (await _spend_once(client, upstream, other)).status_code == 200
    assert len(gateway.usage("run1")) == 1
    assert gateway.spend("run1") > 0


async def test_cost_log_jsonl(client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams,
                              tmp_path: Path) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    await _spend_once(client, upstream, key)
    await _spend_once(client, upstream, key)
    lines = (tmp_path / "cost.jsonl").read_text().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["run_id"] == "run1" and row["role"] == "dev" and row["model"] == CLAUDE
    assert row["input_tokens"] == 1000 and row["output_tokens"] == 500
    assert row["cache_read_tokens"] == 2000 and row["status"] == 200
    assert row["cost_usd"] == pytest.approx(0.0111) and row["ts"]


async def test_upstream_error_status_passes_through_and_is_logged(
        client: httpx.AsyncClient, gateway: Gateway, upstream: Upstreams) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    upstream.respond(httpx.Response(429, json={"error": {"type": "rate_limit_error"}}))
    r = await client.post("/anthropic/messages", json=_anthropic_body(),
                          headers=_anthropic_headers(key))
    assert r.status_code == 429 and r.json()["error"]["type"] == "rate_limit_error"
    (rec,) = gateway.usage("run1")
    assert rec.status == 429 and rec.cost_usd == 0.0


async def test_upstream_connection_error_is_502(client: httpx.AsyncClient, gateway: Gateway,
                                                upstream: Upstreams) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)
    upstream.responder = boom
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    r = await client.post("/anthropic/messages", json=_anthropic_body(),
                          headers=_anthropic_headers(key))
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "bad_gateway"
    assert REAL_ANTHROPIC not in r.text


# ── end to end over a real socket ───────────────────────────────────────────
async def test_e2e_real_sse_stream_through_gateway(tmp_path: Path) -> None:
    with FakeLLM() as fake:
        gw = Gateway({"fake": Upstream("fake", "openai_compatible", fake.base_url, "real-key")},
                     PRICES.get, cost_log=tmp_path / "cost.jsonl")
        http = await serve_app(gw.app())
        try:
            key = gw.issue_key("run1", "dev", ["fake-model"])
            async with httpx.AsyncClient() as c:
                async with c.stream(
                        "POST", f"{http.url}/fake/chat/completions",
                        headers={"authorization": f"Bearer {key}"},
                        json={"model": "fake-model", "stream": True,
                              "messages": [{"role": "user", "content": "hi"}]}) as r:
                    assert r.status_code == 200
                    assert r.headers["content-type"].startswith("text/event-stream")
                    lines = [line async for line in r.aiter_lines() if line.startswith("data:")]
            assert lines[-1] == "data: [DONE]"
            chunks = [json.loads(line[5:]) for line in lines[:-1]]
            assert chunks[0]["choices"][0]["delta"]["content"] == "Fake session"
            assert chunks[-1]["usage"]["prompt_tokens"] == 10
            (rec,) = gw.usage("run1")
            assert (rec.input_tokens, rec.output_tokens, rec.status) == (10, 5, 200)
            assert rec.cost_usd == pytest.approx(10 * 0.01 / 1000 + 5 * 0.02 / 1000)
            assert len((tmp_path / "cost.jsonl").read_text().splitlines()) == 1
            assert fake.requests  # the request really reached the upstream
        finally:
            await http.stop()
            await gw.aclose()


async def test_cost_log_rows_carry_the_run_label(client: httpx.AsyncClient, gateway: Gateway,
                                                 upstream: Upstreams, tmp_path: Path) -> None:
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    gateway.label("run1", "dev", card="T3", session="kept", carried_tokens=41_000, turn=7)
    await _spend_once(client, upstream, key)
    row = json.loads((tmp_path / "cost.jsonl").read_text().splitlines()[0])
    assert (row["card"], row["session"], row["carried_tokens"], row["turn"]) == (
        "T3", "kept", 41_000, 7)
    assert row["cold"] is False                     # the reply had cache reads


def test_cold_call_is_a_big_uncached_context_after_idling(
        gateway: Gateway, monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway import app as gateway_app
    key = gateway.issue_key("run1", "dev", [CLAUDE])
    call = gateway_app._Call(key=gateway._keys[key], upstream=UPSTREAMS["anthropic"],
                             model=CLAUDE, images=0)
    clock = iter([1000.0, 1100.0, 1500.0, 1900.0])
    monkeypatch.setattr(gateway_app.time, "monotonic", lambda: next(clock))
    big = gateway_app._Usage(input_tokens=150_000, output_tokens=10)
    small = gateway_app._Usage(input_tokens=5_000, output_tokens=10)
    gateway._record(call, 200, big)     # first call of the role: nothing to compare
    gateway._record(call, 200, big)     # 100 s later: cache would still be warm
    gateway._record(call, 200, big)     # 400 s idle, no cache read: a cold re-send
    gateway._record(call, 200, small)   # 400 s idle but a small fresh brief
    assert [r.cold for r in gateway.usage("run1")] == [False, False, True, False]
