"""Tests for core.harness.opencode_client using httpx.MockTransport (no network)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx
import pytest

from core.harness.opencode_client import (
    ModelRef,
    OpencodeClient,
    OpencodeError,
    TurnOutcome,
    file_part,
    output_format,
    text_part,
)

DIR = "/work/proj"

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kwargs: object) -> tuple[OpencodeClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapper(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = OpencodeClient(
        "http://oc.test", DIR, transport=httpx.MockTransport(wrapper), **kwargs  # type: ignore[arg-type]
    )
    return client, seen


async def test_directory_param_on_every_call() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/session/status":
            return httpx.Response(200, json={})
        if req.url.path.endswith("/abort"):
            return httpx.Response(200, json=True)
        if req.url.path.endswith("/message") and req.method == "GET":
            return httpx.Response(200, json=[])
        if req.url.path.endswith("/diff"):
            return httpx.Response(200, json=[])
        if req.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_1"})
        if req.url.path.endswith("/message"):
            return httpx.Response(200, json={"info": {}, "parts": []})
        return httpx.Response(204)

    client, seen = make_client(handler)
    async with client:
        assert await client.health()
        await client.create_session()
        await client.prompt("ses_1", [text_part("x")])
        await client.prompt_async("ses_1", [text_part("x")])
        await client.messages("ses_1")
        assert await client.abort("ses_1") is True
        await client.session_status()
        await client.session_diff("ses_1")
        await client.reply_permission("per_1", "once")
    assert len(seen) == 9
    assert all(r.url.params["directory"] == DIR for r in seen)


async def test_basic_auth_present_when_password_set() -> None:
    client, seen = make_client(lambda r: httpx.Response(200), password="pw")
    async with client:
        await client.health()
    expected = "Basic " + base64.b64encode(b"opencode:pw").decode()
    assert seen[0].headers["authorization"] == expected


async def test_custom_username() -> None:
    client, seen = make_client(lambda r: httpx.Response(200), password="pw", username="bob")
    async with client:
        await client.health()
    assert seen[0].headers["authorization"] == "Basic " + base64.b64encode(b"bob:pw").decode()


async def test_no_auth_header_without_password() -> None:
    client, seen = make_client(lambda r: httpx.Response(200))
    async with client:
        await client.health()
    assert "authorization" not in seen[0].headers


async def test_create_session_omits_none() -> None:
    client, seen = make_client(lambda r: httpx.Response(200, json={"id": "ses_a"}))
    async with client:
        out = await client.create_session(title="t")
    assert out == {"id": "ses_a"}
    assert json.loads(seen[0].content) == {"title": "t"}


async def test_create_session_full_body() -> None:
    perms = [{"permission": "edit", "pattern": "*", "action": "deny"}]
    client, seen = make_client(lambda r: httpx.Response(200, json={"id": "ses_a"}))
    async with client:
        await client.create_session(title="t", agent="build", permission=perms, parent_id="ses_p")
    assert json.loads(seen[0].content) == {
        "title": "t",
        "agent": "build",
        "permission": perms,
        "parentID": "ses_p",
    }


async def test_prompt_body_and_outcome() -> None:
    resp = {
        "info": {
            "structured": {"ok": True},
            "cost": 0.25,
            "tokens": {"input": 1, "output": 2, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "finish": "stop",
        },
        "parts": [],
    }
    client, seen = make_client(lambda r: httpx.Response(200, json=resp))
    schema = {"type": "object"}
    async with client:
        out = await client.prompt(
            "ses_1",
            [text_part("hi")],
            agent="plan",
            model=ModelRef("anthropic", "sonnet"),
            system="sys",
            tools={"bash": False},
            format=output_format(schema, retry_count=2),
        )
    req = seen[0]
    assert req.method == "POST" and req.url.path == "/session/ses_1/message"
    assert json.loads(req.content) == {
        "parts": [{"type": "text", "text": "hi"}],
        "agent": "plan",
        "model": {"providerID": "anthropic", "modelID": "sonnet"},
        "system": "sys",
        "tools": {"bash": False},
        "format": {"type": "json_schema", "schema": schema, "retryCount": 2},
    }
    assert out.structured == {"ok": True}
    assert out.cost == 0.25
    assert out.tokens["output"] == 2
    assert out.finish == "stop"
    assert out.error_name is None and out.raw == resp


async def test_prompt_minimal_body_omits_none() -> None:
    client, seen = make_client(lambda r: httpx.Response(200, json={"info": {}, "parts": []}))
    async with client:
        await client.prompt("ses_1", [text_part("hi")])
    assert json.loads(seen[0].content) == {"parts": [{"type": "text", "text": "hi"}]}


def test_turn_outcome_error() -> None:
    out = TurnOutcome.from_response(
        {
            "info": {
                "error": {
                    "name": "StructuredOutputError",
                    "data": {"message": "bad", "retries": 0},
                }
            }
        }
    )
    assert out.error_name == "StructuredOutputError"
    assert out.error_message == "bad"
    assert out.structured is None and out.cost == 0.0 and out.tokens == {}


def test_turn_outcome_missing_keys() -> None:
    out = TurnOutcome.from_response({})
    assert out.structured is None
    assert out.error_name is None and out.error_message is None
    assert out.cost == 0.0 and out.tokens == {} and out.finish is None
    out2 = TurnOutcome.from_response({"info": {"error": {"name": "X"}, "cost": None}})
    assert out2.error_name == "X" and out2.error_message is None and out2.cost == 0.0


def test_part_helpers() -> None:
    assert file_part("file:///a", "text/plain") == {
        "type": "file",
        "mime": "text/plain",
        "url": "file:///a",
    }
    assert file_part("file:///a", "text/plain", "a")["filename"] == "a"
    assert output_format({"x": 1})["retryCount"] == 0


@pytest.mark.parametrize("status", [200, 204])
async def test_prompt_async_accepts_2xx(status: int) -> None:
    client, seen = make_client(lambda r: httpx.Response(status))
    async with client:
        assert await client.prompt_async("ses_1", [text_part("x")], agent="a") is None
    assert seen[0].url.path == "/session/ses_1/prompt_async"
    assert json.loads(seen[0].content) == {"parts": [{"type": "text", "text": "x"}], "agent": "a"}


async def test_non_2xx_raises_with_status_and_body() -> None:
    client, _ = make_client(lambda r: httpx.Response(401, text="nope"))
    async with client:
        with pytest.raises(OpencodeError) as ei:
            await client.create_session()
    assert ei.value.status_code == 401
    assert ei.value.body == "nope"


async def test_transport_error_wrapped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client, _ = make_client(handler)
    async with client:
        with pytest.raises(OpencodeError) as ei:
            await client.messages("ses_1")
    assert ei.value.status_code is None


async def test_health_false_on_error() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client, _ = make_client(boom)
    async with client:
        assert await client.health() is False
    client2, _ = make_client(lambda r: httpx.Response(500))
    async with client2:
        assert await client2.health() is False


async def test_reply_permission_body() -> None:
    client, seen = make_client(lambda r: httpx.Response(200, json=True))
    async with client:
        await client.reply_permission("per_9", "reject", message="no")
        await client.reply_permission("per_9", "always")
    assert seen[0].url.path == "/permission/per_9/reply"
    assert json.loads(seen[0].content) == {"reply": "reject", "message": "no"}
    assert json.loads(seen[1].content) == {"reply": "always"}


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for c in self._chunks:
            yield c


async def test_events_parses_sse() -> None:
    e1 = {"type": "session.idle", "properties": {"sessionID": "ses_1"}}
    e2 = {"type": "permission.asked", "properties": {"id": "per_1"}}
    e3 = {"type": "message.part.updated", "properties": {"n": 3}}
    raw = (
        b": connected\n\n"
        + b"data: "
        + json.dumps(e1).encode()
        + b"\n\n\n"
        + b": keep-alive\n\n"
        + b"data: "
        + json.dumps(e2).encode()
        + b"\n\n"
        + b"data: "
        + json.dumps(e3).encode()
        + b"\n\n"
    )
    # Split mid-line inside the second data payload.
    cut = raw.index(b"permission") + 4
    chunks = [raw[:7], raw[7:cut], raw[cut:]]

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/event"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Chunks(chunks)
        )

    client, seen = make_client(handler)
    async with client:
        got = [e async for e in client.events()]
    assert got == [e1, e2, e3]
    assert seen[0].url.params["directory"] == DIR


async def test_events_non_2xx_raises() -> None:
    client, _ = make_client(lambda r: httpx.Response(401, text="unauth"))
    async with client:
        with pytest.raises(OpencodeError) as ei:
            async for _ in client.events():
                pass
    assert ei.value.status_code == 401 and ei.value.body == "unauth"
