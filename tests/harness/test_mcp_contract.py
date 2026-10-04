"""Contract: real opencode talks to GuudFlow's MCP server over remote MCP (gotcha #12)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.harness.mcp_server import McpIdentity, McpServer, McpTool, ToolResult, serve_app
from core.harness.opencode_client import OpencodeClient, text_part
from core.harness.result_inbox import ResultInbox
from core.harness.results import AgentResult
from core.harness.server import OpencodeServer
from tests.harness.conftest import LOCKED_PERMISSION, init_repo, locked_config, requires_opencode
from tests.harness.fake_llm import FakeLLM, ToolCall

pytestmark = requires_opencode


async def test_submit_result_over_remote_mcp(tmp_path: Path, fake_llm: FakeLLM) -> None:
    mcp = McpServer()
    inbox = ResultInbox()
    mcp.register(inbox.tool())

    async def whoami(identity: McpIdentity, args: dict[str, Any]) -> ToolResult:
        return ToolResult(f"you are {identity.role} in {identity.run_id}")

    mcp.register(McpTool("whoami", "Report the caller.", {"type": "object", "properties": {}},
                         whoami))
    mcp.register(McpTool("secret", "Not granted.", {"type": "object", "properties": {}}, whoami))
    calls: list[tuple[str, bool]] = []
    mcp.on_call(lambda ident, name, args, result: calls.append((name, result.is_error)))

    identity = McpIdentity("run-1", "builder", frozenset({"submit_result", "whoami"}))
    inbox.expect("run-1", "builder", AgentResult)
    token = mcp.issue_token(identity)
    http = await serve_app(mcp.app())
    try:
        config = locked_config(
            fake_llm, permission={**LOCKED_PERMISSION, "guudflow_*": "allow"},
            mcp={"guudflow": {"type": "remote", "url": http.url + "/", "enabled": True,
                              "oauth": False, "headers": {"Authorization": f"Bearer {token}"}}})
        fake_llm.script(
            ToolCall("guudflow_whoami", {}),
            ToolCall("guudflow_submit_result", {"status": "done", "summary": "too short"}),
            ToolCall("guudflow_submit_result", {"status": "done",
                                                "summary": "Created the module and its tests."}),
            ToolCall("bash", {"command": "git status", "description": "stop"}),
        )
        workdir = init_repo(tmp_path / "work")
        async with OpencodeServer(config=config, home=tmp_path / "home", workdir=workdir) as srv:
            async with OpencodeClient(srv.base_url, str(workdir), password=srv.password) as oc:
                session = await oc.create_session()
                await oc.prompt(session["id"], [text_part("do the task")])
        offered = set(fake_llm.tool_requests()[0]["tools"])
        assert {"guudflow_whoami", "guudflow_submit_result"} <= offered
        assert "guudflow_secret" not in offered, "ungranted MCP tools must not be listed"
        assert calls[:3] == [("whoami", False), ("submit_result", True), ("submit_result", False)]
        result = inbox.take("run-1", "builder")
        assert isinstance(result, AgentResult) and result.summary.startswith("Created")
        # The schema the model saw came from the expected result model.
        spec = next(t for t in fake_llm.tool_requests()[0]["tool_specs"]
                    if t["function"]["name"] == "guudflow_submit_result")
        assert "status" in spec["function"]["parameters"]["properties"]
    finally:
        await http.stop()
