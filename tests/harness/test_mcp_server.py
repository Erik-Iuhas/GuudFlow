"""Tests for core.harness.mcp_server and core.harness.result_inbox."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.harness.mcp_server import McpIdentity, McpServer, McpTool, ToolResult
from core.harness.result_inbox import ResultInbox
from core.harness.results import AgentResult, ReviewResult

BUILDER = McpIdentity(run_id="r1", role="builder", tools=frozenset({"echo", "submit_result"}))
REVIEWER = McpIdentity(run_id="r1", role="reviewer", tools=frozenset({"submit_result"}))


async def _echo(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
    return ToolResult(f"{identity.role}: {args.get('text', '')}")


def _server() -> tuple[McpServer, ResultInbox]:
    server = McpServer()
    inbox = ResultInbox()
    server.register(McpTool("echo", "Echo.", {"type": "object", "properties": {}}, _echo))
    server.register(inbox.tool())
    return server, inbox


def _call(name: str, args: dict[str, Any], mid: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


async def test_initialize_negotiates_version() -> None:
    server, _ = _server()
    reply = await server.handle(BUILDER, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                          "params": {"protocolVersion": "2025-03-26"}})
    assert reply is not None and reply["result"]["protocolVersion"] == "2025-03-26"
    unknown = await server.handle(BUILDER, {"jsonrpc": "2.0", "id": 2, "method": "initialize",
                                            "params": {"protocolVersion": "1999-01-01"}})
    assert unknown is not None and unknown["result"]["protocolVersion"] == "2025-06-18"


async def test_notifications_get_no_reply() -> None:
    server, _ = _server()
    assert await server.handle(BUILDER, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


async def test_tools_list_is_filtered_per_identity() -> None:
    server, _ = _server()
    b = await server.handle(BUILDER, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    r = await server.handle(REVIEWER, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert b and r
    assert [t["name"] for t in b["result"]["tools"]] == ["echo", "submit_result"]
    assert [t["name"] for t in r["result"]["tools"]] == ["submit_result"]


async def test_ungranted_tool_is_refused() -> None:
    server, _ = _server()
    reply = await server.handle(REVIEWER, _call("echo", {"text": "x"}))
    assert reply and reply["result"]["isError"] is True
    assert "not available" in reply["result"]["content"][0]["text"]


async def test_handler_exception_becomes_tool_error() -> None:
    server = McpServer()

    async def boom(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        raise RuntimeError("kaput")

    server.register(McpTool("boom", "Fails.", {"type": "object"}, boom))
    reply = await server.handle(McpIdentity("r", "x", frozenset({"boom"})), _call("boom", {}))
    assert reply and reply["result"]["isError"] and "kaput" in reply["result"]["content"][0]["text"]


async def test_call_hook_sees_every_call() -> None:
    server, _ = _server()
    seen: list[tuple[str, str]] = []
    server.on_call(lambda ident, name, args, result: seen.append((ident.role, name)))
    await server.handle(BUILDER, _call("echo", {"text": "hi"}))
    await server.handle(REVIEWER, _call("echo", {}))
    assert seen == [("builder", "echo"), ("reviewer", "echo")]


async def test_submit_result_schema_follows_expected_model() -> None:
    server, inbox = _server()
    inbox.expect("r1", "reviewer", ReviewResult)
    reply = await server.handle(REVIEWER, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert reply
    schema = reply["result"]["tools"][0]["inputSchema"]
    assert "verdict" in schema["properties"]


async def test_submit_result_rejects_then_accepts() -> None:
    server, inbox = _server()
    inbox.expect("r1", "builder", AgentResult)
    bad = await server.handle(BUILDER, _call("submit_result", {"status": "done", "summary": "short"}))
    assert bad and bad["result"]["isError"] and "summary" in bad["result"]["content"][0]["text"]
    assert inbox.take("r1", "builder") is None
    good = await server.handle(BUILDER, _call("submit_result", {
        "status": "done", "summary": "Implemented the parser and its tests."}))
    assert good and not good["result"]["isError"]
    assert inbox.status("r1", "builder") == (2, None)
    result = inbox.take("r1", "builder")
    assert isinstance(result, AgentResult) and result.status == "done"
    assert inbox.take("r1", "builder") is None


async def test_submit_without_expectation_is_refused() -> None:
    server, inbox = _server()
    reply = await server.handle(BUILDER, _call("submit_result", {"status": "done"}))
    assert reply and reply["result"]["isError"]


async def test_http_requires_valid_token_and_revocation_works() -> None:
    server, _ = _server()
    token = server.issue_token(BUILDER)
    transport = httpx.ASGITransport(app=server.app())
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as http:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert (await http.post("/", json=body)).status_code == 401
        ok = await http.post("/", json=body, headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200 and ok.json()["result"]["tools"]
        note = await http.post("/", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                               headers={"Authorization": f"Bearer {token}"})
        assert note.status_code == 202
        assert (await http.get("/", headers={"Authorization": f"Bearer {token}"})).status_code == 405
        server.revoke_run("r1")
        assert (await http.post("/", json=body,
                                headers={"Authorization": f"Bearer {token}"})).status_code == 401


@pytest.mark.parametrize("payload", [b"not json", b"{"])
async def test_http_parse_error(payload: bytes) -> None:
    server, _ = _server()
    token = server.issue_token(BUILDER)
    transport = httpx.ASGITransport(app=server.app())
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as http:
        resp = await http.post("/", content=payload, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400 and resp.json()["error"]["code"] == -32700
