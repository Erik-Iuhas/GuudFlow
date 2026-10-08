"""Contract tests: GuudFlow's assumptions about the pinned opencode binary.

These run the real `opencode serve` against the scripted FakeLLM (no network,
no API key). If any of these fail after an opencode upgrade, the plan's §2
findings need re-checking before anything else changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.harness.opencode_client import output_format, text_part
from core.harness.results import AgentResult, result_json_schema, validate_result
from tests.harness.conftest import OpencodeEnv, requires_opencode
from tests.harness.fake_llm import Text, ToolCall

pytestmark = requires_opencode

SNAPSHOT = Path(__file__).with_name("opencode_api_snapshot.json")

# The HTTP surface GuudFlow depends on (plan §2.1).
USED_ENDPOINTS = [
    ("post", "/session"),
    ("post", "/session/{sessionID}/message"),
    ("post", "/session/{sessionID}/prompt_async"),
    ("get", "/session/{sessionID}/message"),
    ("post", "/session/{sessionID}/abort"),
    ("get", "/session/status"),
    ("get", "/session/{sessionID}/diff"),
    ("post", "/permission/{requestID}/reply"),
    ("get", "/event"),
    ("get", "/global/health"),
]
USED_EVENTS = ["session.idle", "session.error", "session.status", "permission.asked",
               "permission.replied", "message.updated", "message.part.updated",
               "session.compacted", "file.edited", "todo.updated"]


def _done(summary: str = "Wrote the requested file and verified it.") -> ToolCall:
    return ToolCall("StructuredOutput", {"status": "done", "summary": summary,
                                         "files_changed": []})


def _api_shape(doc: dict[str, Any]) -> dict[str, Any]:
    """Reduce the OpenAPI doc to what GuudFlow relies on."""
    schemas = doc["components"]["schemas"]
    shape: dict[str, Any] = {"endpoints": {}, "events": []}
    for method, path in USED_ENDPOINTS:
        op = doc["paths"][path][method]
        body = (op.get("requestBody", {}).get("content", {})
                .get("application/json", {}).get("schema", {}))
        if "$ref" in body:
            body = schemas[body["$ref"].rsplit("/", 1)[-1]]
        shape["endpoints"][f"{method.upper()} {path}"] = sorted(body.get("properties", {}))
    event_refs = schemas["Event"].get("anyOf") or schemas["Event"].get("oneOf") or []
    types: set[str] = set()
    for ref in event_refs:
        prop = schemas[ref["$ref"].rsplit("/", 1)[-1]]["properties"]["type"]
        types.update(prop.get("enum") or [prop.get("const")])
    shape["events"] = sorted(t for t in USED_EVENTS if t in types)
    return shape


async def test_api_matches_snapshot(opencode_env: OpencodeEnv) -> None:
    """The endpoints, request fields and events we use still exist."""
    async with httpx.AsyncClient(auth=("opencode", opencode_env.server.password)) as http:
        doc = (await http.get(f"{opencode_env.server.base_url}/doc")).json()
    shape = _api_shape(doc)
    if os.environ.get("UPDATE_OPENCODE_SNAPSHOT") == "1" or not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(shape, indent=2) + "\n")
    assert shape == json.loads(SNAPSHOT.read_text())
    assert shape["events"] == sorted(USED_EVENTS), "an event type we rely on disappeared"


async def test_server_requires_password(opencode_env: OpencodeEnv) -> None:
    async with httpx.AsyncClient() as http:
        resp = await http.get(f"{opencode_env.server.base_url}/session")
    assert resp.status_code == 401


async def test_scripted_turn_writes_file_and_returns_structured(opencode_env: OpencodeEnv) -> None:
    """End to end: a tool call edits the workdir, then a valid structured result."""
    env = opencode_env
    target = env.workdir / "hello.txt"
    env.fake.script(ToolCall("write", {"filePath": str(target), "content": "hi\n"}), _done())
    session = await env.client.create_session(title="contract")
    outcome = await env.client.prompt(
        session["id"], [text_part("write hello.txt")],
        format=output_format(result_json_schema(AgentResult)),
    )
    assert target.read_text() == "hi\n"
    result, error = validate_result(AgentResult, outcome.structured)
    assert error is None and result is not None and result.status == "done"
    assert outcome.tokens.get("input", 0) > 0


async def test_plain_text_reply_is_a_structured_output_error(opencode_env: OpencodeEnv) -> None:
    """Gotcha #7: a text reply instead of the tool fails without retrying."""
    env = opencode_env
    env.fake.script(Text("I'm done!"))
    session = await env.client.create_session()
    outcome = await env.client.prompt(session["id"], [text_part("go")],
                                      format=output_format(result_json_schema(AgentResult), 2))
    assert outcome.structured is None
    assert outcome.error_name == "StructuredOutputError"


async def test_invalid_structured_output_is_not_validated_by_opencode(
        opencode_env: OpencodeEnv) -> None:
    """Gotcha #6: opencode accepts schema-violating output; GuudFlow must validate."""
    env = opencode_env
    env.fake.script(ToolCall("StructuredOutput", {"status": "maybe"}))
    session = await env.client.create_session()
    outcome = await env.client.prompt(session["id"], [text_part("go")],
                                      format=output_format(result_json_schema(AgentResult)))
    assert outcome.structured == {"status": "maybe"}
    result, error = validate_result(AgentResult, outcome.structured)
    assert result is None and error


@pytest.mark.opencode_config(permission={"*": "deny", "read": "allow"})
async def test_deny_all_hides_structured_output_tool(opencode_env: OpencodeEnv) -> None:
    """Gotcha #5: without an explicit allow the StructuredOutput tool disappears."""
    env = opencode_env
    env.fake.script(Text("cannot comply"))
    session = await env.client.create_session()
    await env.client.prompt(session["id"], [text_part("go")],
                            format=output_format(result_json_schema(AgentResult)))
    offered = env.fake.tool_requests()[-1]["tools"]
    assert "StructuredOutput" not in offered


async def test_project_config_in_workdir_is_ignored(opencode_env: OpencodeEnv) -> None:
    """Gotcha #2: an agent-written opencode.json must not add agents or permissions."""
    env = opencode_env
    (env.workdir / "opencode.json").write_text(json.dumps(
        {"agent": {"evil": {"mode": "primary", "prompt": "x", "permission": {"*": "allow"}}}}))
    async with httpx.AsyncClient(auth=("opencode", env.server.password)) as http:
        agents = (await http.get(f"{env.server.base_url}/agent",
                                 params={"directory": str(env.workdir)})).json()
    assert "evil" not in {a["name"] for a in agents}


@requires_opencode
async def test_a_stopped_server_leaves_nothing_in_tmp(tmp_path: Path) -> None:
    """opencode unpacks a native library into TMPDIR on start; the server's own TMPDIR is
    removed on stop instead of piling up in /tmp."""
    import tempfile

    from core.harness.server import OpencodeServer
    from tests.harness.conftest import init_repo
    shared = Path(tempfile.gettempdir())
    before = {p.name for p in shared.iterdir() if p.name.endswith(".so")}
    server = OpencodeServer(config={"$schema": "https://opencode.ai/config.json",
                                    "autoupdate": False, "share": "disabled"},
                            home=tmp_path / "home", workdir=init_repo(tmp_path / "work"))
    await server.start()
    assert (tmp_path / "home" / ".tmp").is_dir()
    await server.stop()
    assert not (tmp_path / "home" / ".tmp").exists()
    after = {p.name for p in shared.iterdir() if p.name.endswith(".so")}
    assert after <= before
