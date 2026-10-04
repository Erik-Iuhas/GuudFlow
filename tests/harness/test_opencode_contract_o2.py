"""O2 contract tests: permission round-trips, subagents, images, MCP, compaction.

Each test pins down one opencode behaviour the harness design relies on
(plan §2.2, §5.7, §5.9, §5.10). Runs the real binary against the FakeLLM.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from core.harness.opencode_client import output_format, text_part
from core.harness.results import AgentResult, result_json_schema
from tests.harness.conftest import LOCKED_PERMISSION, OpencodeEnv, requires_opencode
from tests.harness.fake_llm import Text, ToolCall
from tests.harness.fake_mcp import PNG_1X1

pytestmark = requires_opencode

FORMAT = output_format(result_json_schema(AgentResult))
DONE = ToolCall("StructuredOutput", {"status": "done", "summary": "Finished the scripted step."})
EventHook = Callable[[dict[str, Any]], Awaitable[None]]


def _perm(**overrides: Any) -> dict[str, Any]:
    return {**LOCKED_PERMISSION, **overrides}


async def run_turn(env: OpencodeEnv, prompt: str, on_event: EventHook | None = None,
                   timeout: float = 60.0) -> tuple[str, list[dict[str, Any]]]:
    """Subscribe to events, send a prompt asynchronously, wait for session.idle."""
    session = await env.client.create_session()
    sid = session["id"]
    events: list[dict[str, Any]] = []
    connected = asyncio.Event()

    async def listen() -> None:
        async for event in env.client.events():
            connected.set()
            events.append(event)
            if on_event is not None:
                await on_event(event)
            props = event.get("properties") or {}
            if event.get("type") == "session.idle" and props.get("sessionID") == sid:
                return

    listener = asyncio.create_task(listen())
    await asyncio.wait_for(connected.wait(), 10)
    await env.client.prompt_async(sid, [text_part(prompt)], format=FORMAT)
    await asyncio.wait_for(listener, timeout)
    return sid, events


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [e.get("type", "") for e in events]


def _tool_states(events: list[dict[str, Any]], sid: str) -> list[tuple[str, str]]:
    """Final (tool, status) per tool part, in call order, built from the event stream.

    Gotcha #10: GET /session/{id}/message returns 400 once a prompt used `format`,
    so transcripts come from events, not from the message list.
    """
    order: list[str] = []
    final: dict[str, tuple[str, str]] = {}
    for event in events:
        part = (event.get("properties") or {}).get("part") or {}
        if event.get("type") != "message.part.updated" or part.get("type") != "tool":
            continue
        if part.get("sessionID") != sid:
            continue
        if part["id"] not in final:
            order.append(part["id"])
        final[part["id"]] = (part["tool"], (part.get("state") or {}).get("status", ""))
    return [final[i] for i in order if final[i][0] != "StructuredOutput"]


# ── permission.asked round-trip (§5.1 "ask", §5.9 sticky approvals) ─────────

@pytest.mark.opencode_config(permission=_perm(bash={"*": "ask"}))
async def test_permission_ask_once_then_reject(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    first, second = env.workdir / "first.txt", env.workdir / "second.txt"
    env.fake.script(ToolCall("bash", {"command": f"touch {first}", "description": "a"}),
                    ToolCall("bash", {"command": f"touch {second}", "description": "b"}),
                    DONE)
    replies = iter(["once", "reject"])
    asked: list[dict[str, Any]] = []

    async def answer(event: dict[str, Any]) -> None:
        if event.get("type") == "permission.asked":
            asked.append(event["properties"])
            await env.client.reply_permission(event["properties"]["id"], next(replies))

    sid, events = await run_turn(env, "touch two files", answer)
    assert len(asked) == 2
    assert asked[0]["permission"] == "bash"
    assert first.exists() and not second.exists()
    assert "permission.replied" in _types(events)
    assert ("bash", "error") in _tool_states(events, sid)


@pytest.mark.opencode_config(permission=_perm(bash={"*": "ask"}))
async def test_permission_always_stops_repeat_prompts(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    a, b = env.workdir / "a.txt", env.workdir / "b.txt"
    env.fake.script(ToolCall("bash", {"command": f"touch {a}", "description": "a"}),
                    ToolCall("bash", {"command": f"touch {b}", "description": "b"}),
                    DONE)
    asked: list[dict[str, Any]] = []

    async def answer(event: dict[str, Any]) -> None:
        if event.get("type") == "permission.asked":
            asked.append(event["properties"])
            await env.client.reply_permission(event["properties"]["id"], "always")

    await run_turn(env, "touch two files", answer)
    assert a.exists() and b.exists()
    assert len(asked) == 1, "an 'always' reply must cover the same pattern for the session"


# ── abort ────────────────────────────────────────────────────────────────

@pytest.mark.opencode_config(permission=_perm(bash={"*": "allow"}))
async def test_abort_stops_a_running_turn(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    env.fake.script(ToolCall("bash", {"command": "sleep 30", "description": "slow"}), DONE)

    async def abort_on_tool(event: dict[str, Any]) -> None:
        props = event.get("properties") or {}
        part = props.get("part") or {}
        if part.get("type") == "tool" and (part.get("state") or {}).get("status") == "running":
            await env.client.abort(part["sessionID"])

    start = asyncio.get_running_loop().time()
    sid, events = await run_turn(env, "sleep", abort_on_tool, timeout=25)
    assert asyncio.get_running_loop().time() - start < 20
    assert ("bash", "completed") not in _tool_states(events, sid)


# ── edit scope patterns are project-relative (§5.1) ──────────────────────

@pytest.mark.opencode_config(permission=_perm(edit={"*": "deny", "src/**": "allow",
                                                    "src/secret/**": "deny"}))
async def test_edit_scope_applies_to_write_tool(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    (env.workdir / "src" / "secret").mkdir(parents=True)
    ok, outside, protected = (env.workdir / "src" / "a.py", env.workdir / "b.py",
                              env.workdir / "src" / "secret" / "c.py")
    env.fake.script(ToolCall("write", {"filePath": str(ok), "content": "x"}),
                    ToolCall("write", {"filePath": str(outside), "content": "x"}),
                    ToolCall("write", {"filePath": str(protected), "content": "x"}),
                    DONE)
    await run_turn(env, "write three files")
    assert ok.exists()
    assert not outside.exists()
    assert not protected.exists(), "a later deny must win over an earlier allow"


# ── subagents: explore only, depth 1 (§5.7, §5.9) ────────────────────────

EXPLORE_READ_ONLY = {"agent": {"explore": {"permission": {
    "task": "deny", "edit": "deny", "bash": {"*": "deny", "git status*": "allow"}}}}}


@pytest.mark.opencode_config(permission=_perm(task={"*": "deny", "explore": "allow"}),
                             subagent_depth=1, **EXPLORE_READ_ONLY)
async def test_task_restricted_to_explore(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    # The explore subagent tries to spawn its own explore subagent: depth 1 must stop it.
    env.fake.route("SUBAGENT-EXPLORE",
                   ToolCall("task", {"description": "nested", "prompt": "NESTED look deeper",
                                     "subagent_type": "explore"}),
                   Text("explore found nothing"))
    env.fake.route("NESTED", Text("nested ran"))
    env.fake.script(
        ToolCall("task", {"description": "look", "prompt": "SUBAGENT-GENERAL look around",
                          "subagent_type": "general"}),
        ToolCall("task", {"description": "look", "prompt": "SUBAGENT-EXPLORE look around",
                          "subagent_type": "explore"}),
        DONE)
    sid, events = await run_turn(env, "delegate")
    states = _tool_states(events, sid)
    assert states[0] == ("task", "error"), "general subagent must be denied"
    assert states[1] == ("task", "completed"), "explore subagent must be allowed"
    def first_user(r: dict[str, Any]) -> str:
        return str(next((m.get("content") for m in r["messages"] if m.get("role") == "user"), ""))

    nested = [r for r in env.fake.requests if "NESTED look deeper" in first_user(r)]
    assert not nested, "the explore subagent must not spawn further agents"
    # subagent_depth alone does not stop nesting (gotcha #11); the per-agent override does,
    # and also keeps explore read-only.
    explore_tools = [r["tools"] for r in env.fake.requests
                     if "SUBAGENT-EXPLORE" in first_user(r) and r["tools"]]
    assert explore_tools and all("task" not in t and "edit" not in t for t in explore_tools)


# ── images (§5.10) ───────────────────────────────────────────────────────

def _has_image(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image", "file"):
                    return True
    return False


async def test_read_png_reaches_the_model_as_an_image(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    png = env.workdir / "shot.png"
    png.write_bytes(base64.b64decode(PNG_1X1))
    env.fake.script(ToolCall("read", {"filePath": str(png)}), DONE)
    await run_turn(env, "look at the screenshot")
    after_read = env.fake.tool_requests()[1]["messages"]
    assert _has_image(after_read)


def _mcp_config() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return {"guudflow": {"type": "local", "command": [sys.executable, "-m", "tests.harness.fake_mcp"],
                         "cwd": str(root), "enabled": True}}


@pytest.mark.opencode_config(permission=_perm(guudflow_echo="allow", guudflow_pixel="allow"),
                             mcp=_mcp_config())
async def test_mcp_tools_named_server_underscore_tool(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    env.fake.script(ToolCall("guudflow_echo", {"text": "hi"}),
                    ToolCall("guudflow_pixel", {}), DONE)
    sid, events = await run_turn(env, "use mcp")
    offered = env.fake.tool_requests()[0]["tools"]
    assert {"guudflow_echo", "guudflow_pixel"} <= set(offered)
    assert _tool_states(events, sid) == [("guudflow_echo", "completed"),
                                            ("guudflow_pixel", "completed")]
    # MCP image results reach the model as images (plan §5.10 open question, resolved).
    after_pixel = env.fake.tool_requests()[2]["messages"]
    assert _has_image(after_pixel)


@pytest.mark.opencode_config(permission=_perm(**{"guudflow_*": "allow"}), mcp=_mcp_config())
async def test_mcp_wildcard_permission_key(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    env.fake.script(DONE)
    await run_turn(env, "noop")
    assert "guudflow_echo" in env.fake.tool_requests()[0]["tools"]


@pytest.mark.opencode_config(mcp=_mcp_config())
async def test_mcp_tools_hidden_under_deny_all(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    env.fake.script(DONE)
    await run_turn(env, "noop")
    assert "guudflow_echo" not in env.fake.tool_requests()[0]["tools"]


# ── compaction under deny-all (gotcha #9) ────────────────────────────────

async def test_compaction_works_under_deny_all(opencode_env: OpencodeEnv) -> None:
    env = opencode_env
    env.fake.script(DONE)
    sid, _ = await run_turn(env, "do one thing")
    compacted = asyncio.Event()

    async def listen() -> None:
        async for event in env.client.events():
            if event.get("type") == "session.compacted":
                compacted.set()
                return

    listener = asyncio.create_task(listen())
    await asyncio.sleep(0.5)
    await env.client._json("POST", f"/session/{sid}/summarize",  # noqa: SLF001
                           body={"providerID": "fake", "modelID": "fake-model"})
    await asyncio.wait_for(compacted.wait(), 30)
    listener.cancel()


# ── gotcha #10: message listing breaks once `format` is used ─────────────

async def test_message_listing_fails_after_structured_prompt(opencode_env: OpencodeEnv) -> None:
    """Pins an opencode 1.18.34 bug. If this starts failing after an upgrade, the
    bug is fixed: consider reading transcripts from GET /session/{id}/message again."""
    env = opencode_env
    env.fake.script(DONE)
    session = await env.client.create_session()
    await env.client.prompt(session["id"], [text_part("go")], format=FORMAT)
    with pytest.raises(Exception) as info:
        await env.client.messages(session["id"])
    assert getattr(info.value, "status_code", None) == 400
