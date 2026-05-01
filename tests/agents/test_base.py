"""Tests for agents/base.py — the BaseAgent contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.base import (
    BaseAgent,
    ClaimAdvice,
    ToolDenied,
    ToolNotAvailable,
)
from agents.persona import Persona
from agents.tools.base import BaseTool
from agents.tools.fs import FsRead, FsWrite
from core.context_manager import ContextManager
from core.credentials_broker import CredentialsBroker
from core.file_workspace import FileWorkspace
from core.protocol import FileChange, VerifyResult
from core.signals import Signal, SignalType
from core.tool_manifest import (
    LocalToolEntry,
    MCPToolEntry,
    RoleRBAC,
    ToolDescriptor,
    ToolManifest,
    ToolManifestBuilder,
)
from core.workspace import AgentState, Workspace


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    fw = FileWorkspace(tmp_path / "ws")
    return Workspace(run_id="r1", goal="x", files=fw)


@pytest.fixture
def fs_workspace(tmp_path: Path) -> FileWorkspace:
    return FileWorkspace(tmp_path / "ws")


def _agent_with_manifest(role: str, descriptors: list[ToolDescriptor],
                         tools: dict[str, BaseTool] | None = None) -> BaseAgent:
    a = BaseAgent(role=role)
    a._manifest = ToolManifest(descriptors=descriptors)
    a._local_tools = tools or {}
    return a


# ── Signals ────────────────────────────────────────────────────────────────


async def test_signal_post_basic(workspace: Workspace):
    a = BaseAgent(role="developer")
    sig = await a.signal(workspace, SignalType.STARTED, "started work")
    assert sig in workspace.board
    assert sig.from_agent == "developer"
    assert sig.summary == "started work"


async def test_signal_namespaces_with_persona_name(workspace: Workspace):
    persona = Persona.empty("aria-v1", "Aria", "React")
    a = BaseAgent(role="developer", persona=persona)
    sig = await a.signal(workspace, SignalType.STARTED, "wrote 3 files")
    assert sig.summary == "Aria: wrote 3 files"


# ── ask_human / check_user_board ───────────────────────────────────────────


async def test_ask_human_sets_state_and_pending(workspace: Workspace):
    a = BaseAgent(role="developer")
    await a.ask_human(workspace, "Should this support mobile?")
    assert workspace.pending_human is not None
    assert workspace.pending_human.from_agent == "developer"
    assert workspace.agent_states["developer"] == AgentState.WAITING_FOR_HUMAN
    types = [s.type for s in workspace.board]
    assert SignalType.HUMAN_NEEDED in types


async def test_check_user_board_marks_seen(workspace: Workspace):
    workspace.post_signal(Signal(type=SignalType.USER_COMMENT, summary="add a logo"))
    a = BaseAgent(role="developer")
    unseen = await a.check_user_board(workspace)
    assert len(unseen) == 1
    # Re-checking yields nothing
    again = await a.check_user_board(workspace)
    assert again == []


# ── request_visual_feedback / request_package ──────────────────────────────


async def test_request_visual_feedback_posts_pending(workspace: Workspace):
    a = BaseAgent(role="visual")
    await a.request_visual_feedback(workspace, "renders/a.png", viewport="desktop")
    assert any(s.from_agent == "visual" for s in workspace.pending_screenshots)
    types = [s.type for s in workspace.board]
    assert SignalType.SCREENSHOT_READY in types


async def test_request_package_routes_to_it(workspace: Workspace):
    a = BaseAgent(role="developer")
    await a.request_package(workspace, "requests==2.31.0", "need HTTP client")
    assert workspace.pending_package_requests
    assert workspace.pending_package_requests[0].package == "requests==2.31.0"


# ── Protocol emit / read ───────────────────────────────────────────────────


async def test_emit_commit_and_read_protocol(workspace: Workspace):
    a = BaseAgent(role="developer")
    await a.emit_commit(
        workspace,
        commit_hash="abc1234",
        files=[FileChange("src/auth.py", "new", "OAuth flow")],
        checkpoints=["auth_flow", "token_expiry"],
    )
    # Reader sees it parsed
    b = BaseAgent(role="reviewer")
    msgs = await b.read_protocol(workspace, types={"COMMIT"})
    assert len(msgs) == 1
    assert msgs[0].type == "COMMIT"
    assert msgs[0].commit_hash == "abc1234"


async def test_emit_verify_and_action(workspace: Workspace):
    a = BaseAgent(role="tester")
    await a.emit_verify(
        workspace,
        commit_hash="abc",
        results=[
            VerifyResult("auth_flow", "PASS"),
            VerifyResult("token_expiry", "FAIL", "exp claim is missing from the JWT payload"),
        ],
        action=("developer", "FIX", "token_expiry"),
    )
    b = BaseAgent(role="developer")
    msgs = await b.read_protocol(workspace, types={"VERIFY_RESULT"})
    assert msgs and msgs[0].parsed["action"]["verb"] == "FIX"


async def test_read_protocol_filters_unseen(workspace: Workspace):
    a = BaseAgent(role="developer")
    await a.emit_action(workspace, "tester", "REJECT", "x")
    b = BaseAgent(role="reviewer")
    first = await b.read_protocol(workspace, types={"ACTION"})
    assert len(first) == 1
    second = await b.read_protocol(workspace, types={"ACTION"})
    assert second == []  # already seen


# ── File coordination ──────────────────────────────────────────────────────


async def test_claim_and_release_emit_signals(workspace: Workspace):
    a = BaseAgent(role="developer")
    await a.claim_files(workspace, ["src/auth.py"], intent="edit", reason="add refresh")
    types = [s.type for s in workspace.board]
    assert SignalType.FILE_CLAIM in types

    await a.release_files(workspace, ["src/auth.py"], outcome="committed", commit_hash="abc")
    types = [s.type for s in workspace.board]
    assert SignalType.FILE_RELEASE in types


async def test_active_claims_excludes_released(workspace: Workspace):
    a = BaseAgent(role="developer")
    await a.claim_files(workspace, ["src/auth.py"])
    assert a.active_claims(workspace, "src/auth.py")
    await a.release_files(workspace, ["src/auth.py"])
    assert a.active_claims(workspace, "src/auth.py") == []


async def test_pre_write_check_advice(workspace: Workspace):
    a = BaseAgent(role="alice")
    b = BaseAgent(role="bob")

    # Nothing claimed → proceed
    assert a.pre_write_check(workspace, "x.py").advice == "proceed"

    # Bob claims; alice's pre-write check says await_release
    await b.claim_files(workspace, ["x.py"])
    advice = a.pre_write_check(workspace, "x.py")
    assert advice.advice == "await_release"
    assert advice.blocking_agent == "bob"

    # If alice herself has claimed, no blocker
    advice_b = b.pre_write_check(workspace, "x.py")
    assert advice_b.advice == "proceed"


# ── Tool dispatch ──────────────────────────────────────────────────────────


async def test_call_tool_local(workspace: Workspace, fs_workspace: FileWorkspace):
    await fs_workspace.write("a.py", "x = 1")
    a = _agent_with_manifest(
        role="developer",
        descriptors=[ToolDescriptor("fs-read", "read", {}, "local")],
        tools={"fs-read": FsRead(fs_workspace)},
    )
    res = await a.call_tool(workspace, "fs-read", {"path": "a.py"})
    assert res["ok"] is True
    assert res["content"] == "x = 1"


async def test_call_tool_not_in_manifest_raises(workspace: Workspace):
    a = _agent_with_manifest("developer", descriptors=[])
    with pytest.raises(ToolNotAvailable):
        await a.call_tool(workspace, "fs-read", {})


async def test_call_tool_local_not_instantiated_raises(workspace: Workspace):
    a = _agent_with_manifest(
        "developer",
        descriptors=[ToolDescriptor("fs-read", "x", {}, "local")],
        tools={},  # missing
    )
    with pytest.raises(Exception):
        await a.call_tool(workspace, "fs-read", {})


async def test_call_tool_broker_routed(workspace: Workspace):
    rbac = {"developer": RoleRBAC(role="developer", allowed_scopes=["mcp-github-read"])}
    broker = CredentialsBroker(rbac=rbac)
    async def handler(op: str, payload: dict) -> dict:
        return {"pr": payload.get("id"), "title": "Fix auth"}
    broker.register_handler("mcp-github-read", handler)

    a = _agent_with_manifest(
        "developer",
        descriptors=[ToolDescriptor(
            "mcp-github-get_pr", "Get a PR", {}, "broker",
            scope="mcp-github-read", server="github",
        )],
    )
    a._broker = broker

    res = await a.call_tool(workspace, "mcp-github-get_pr", {"id": 42})
    assert res == {"pr": 42, "title": "Fix auth"}


async def test_call_tool_broker_denied_raises(workspace: Workspace):
    """Manifest contains the descriptor but broker denies — agent sees ToolDenied."""
    broker = CredentialsBroker(rbac={"developer": RoleRBAC(role="developer", allowed_scopes=[])})
    a = _agent_with_manifest(
        "developer",
        descriptors=[ToolDescriptor(
            "mcp-github-get_pr", "Get a PR", {}, "broker",
            scope="mcp-github-read", server="github",
        )],
    )
    a._broker = broker
    with pytest.raises(ToolDenied):
        await a.call_tool(workspace, "mcp-github-get_pr", {"id": 1})


async def test_invoke_mcp_sugar(workspace: Workspace):
    rbac = {"dev": RoleRBAC(role="dev", allowed_scopes=["mcp-github"])}
    broker = CredentialsBroker(rbac=rbac)
    async def handler(op: str, p: dict) -> dict:
        return {"op": op}
    broker.register_handler("mcp-github", handler)

    a = _agent_with_manifest(
        "dev",
        descriptors=[ToolDescriptor(
            "mcp-github-list_issues", "list", {}, "broker",
            scope="mcp-github-list_issues", server="github",
        )],
    )
    a._broker = broker
    res = await a.invoke_mcp(workspace, "github", "list_issues", {})
    assert res == {"op": "mcp-github-list_issues"}


# ── Effective system prompt — layer order ─────────────────────────────────


async def test_effective_system_prompt_layer_order():
    persona = Persona.empty(
        "aria-v1", "Aria", "React frontend",
        character="Methodical and quietly proud.",
        stake_framing="Real users will see this.",
    )
    a = BaseAgent(
        role="developer",
        system_prompt="You are a senior React developer.",
        persona=persona,
    )
    a._project_rules = "TypeScript only. No raw CSS."
    a._manifest = ToolManifest([
        ToolDescriptor("fs-read", "Read file", {}, "local"),
    ])
    a._current_task = "Implement login screen"

    text = await a.effective_system_prompt(teammate_brief="Working with: tester (Rex)")

    # Must contain in order: identity, character, stake, teammates, project_rules,
    # base prompt, tools, CoT primer, positive flourish.
    idx_identity = text.index("Aria")
    idx_character = text.index("Methodical")
    idx_stake = text.index("Real users")
    idx_teammate = text.index("YOUR TEAMMATES")
    idx_rules = text.index("PROJECT RULES")
    idx_base = text.index("senior React")
    idx_tools = text.index("AVAILABLE TOOLS")
    idx_cot = text.index("Before producing output")
    idx_flourish = text.index("proud of")

    indices = [idx_identity, idx_character, idx_stake, idx_teammate,
               idx_rules, idx_base, idx_tools, idx_cot, idx_flourish]
    assert indices == sorted(indices), "layers out of order"


async def test_effective_system_prompt_without_persona():
    """Anonymous agents fall back to role/description for layer 1."""
    a = BaseAgent(role="developer", description="builds things",
                  system_prompt="Do the thing.")
    text = await a.effective_system_prompt()
    assert "developer" in text
    assert "Do the thing" in text


async def test_effective_system_prompt_skips_empty_layers():
    """Empty character/stake/teammates/rules don't produce blank sections."""
    a = BaseAgent(role="x", system_prompt="prompt")
    text = await a.effective_system_prompt()
    assert "YOUR TEAMMATES" not in text
    assert "PROJECT RULES" not in text


# ── Track 1c — Layer 4.5: repo_map injection ──────────────────────────────


def _ws_with_repo_map(entries: list[tuple[str, list[str]]]):
    """Build a Workspace with a populated RepoMap from (path, symbols) tuples."""
    from core.workspace import RepoMap, RepoMapEntry, Workspace

    ws = Workspace(run_id="r-1c", goal="test repo_map layer")
    ws.repo_map = RepoMap(
        entries=[
            RepoMapEntry(path=p, type="py", symbols=syms, rank=1.0)
            for p, syms in entries
        ]
    )
    return ws


async def test_repo_map_layer_present_when_map_populated():
    """A populated repo_map produces a CODEBASE STRUCTURE section listing paths."""
    ws = _ws_with_repo_map([
        ("src/auth.py", ["login", "logout"]),
        ("src/db.py", ["connect"]),
    ])
    a = BaseAgent(role="developer", system_prompt="prompt")
    text = await a.effective_system_prompt(workspace=ws)

    assert "CODEBASE STRUCTURE" in text
    assert "src/auth.py" in text
    assert "src/db.py" in text


async def test_repo_map_layer_absent_when_map_empty():
    """Empty repo_map suppresses the layer cleanly."""
    from core.workspace import RepoMap, Workspace

    ws = Workspace(run_id="r-1c", goal="empty")
    ws.repo_map = RepoMap(entries=[])
    a = BaseAgent(role="developer", system_prompt="prompt")
    text = await a.effective_system_prompt(workspace=ws)

    assert "CODEBASE STRUCTURE" not in text


async def test_repo_map_layer_absent_when_workspace_none():
    """No workspace → no layer (existing behavior preserved)."""
    a = BaseAgent(role="developer", system_prompt="prompt")
    text = await a.effective_system_prompt()  # no workspace passed
    assert "CODEBASE STRUCTURE" not in text


async def test_repo_map_layer_uses_self_workspace_fallback():
    """When workspace isn't passed, falls back to self._workspace
    (set by the runner before run())."""
    ws = _ws_with_repo_map([("src/main.py", ["main"])])
    a = BaseAgent(role="developer", system_prompt="prompt")
    a._workspace = ws

    text = await a.effective_system_prompt()  # no explicit workspace
    assert "CODEBASE STRUCTURE" in text
    assert "src/main.py" in text


async def test_repo_map_layer_respects_budget():
    """Render is hard-capped at ~1500 tokens regardless of entry count."""
    from core.repo_map import _approx_tokens

    # Many entries with long symbol names — should still be bounded.
    entries = [
        (f"src/module_{i:03d}/file.py", [f"function_with_a_long_name_{j}" for j in range(8)])
        for i in range(200)
    ]
    ws = _ws_with_repo_map(entries)
    a = BaseAgent(role="developer", system_prompt="prompt")
    text = await a.effective_system_prompt(workspace=ws)

    # Find the section and measure just its body.
    assert "CODEBASE STRUCTURE" in text
    # Whole prompt may have other layers, but the rendered body inside this layer
    # should not exceed render_compact's max_tokens=1500 by more than the small
    # banner overhead (~30 tokens).
    section_start = text.index("CODEBASE STRUCTURE")
    # The next layer (system_prompt: "prompt") follows — find a unique marker.
    # Safer measure: section length itself.
    section_end = text.find("PROTOCOL VOICE", section_start)
    assert section_end > section_start
    section = text[section_start:section_end]
    assert _approx_tokens(section) <= 1700  # 1500 budget + banner overhead


async def test_repo_map_layer_position_after_memory_before_base_prompt():
    """Layer 4.5 appears after memory recall and before the base prompt."""
    from agents.persona import Persona

    persona = Persona.empty("dev-v1", "Dev", "describe", character="c", stake_framing="s")
    ws = _ws_with_repo_map([("src/x.py", ["foo"])])
    a = BaseAgent(
        role="developer",
        system_prompt="MARKER_BASE_PROMPT",
        persona=persona,
    )
    a._current_task = "do work"
    a._workspace = ws

    text = await a.effective_system_prompt()

    idx_repomap = text.index("CODEBASE STRUCTURE")
    idx_base = text.index("MARKER_BASE_PROMPT")
    assert idx_repomap < idx_base, "repo_map layer must come before base prompt"


# ── Recall ─────────────────────────────────────────────────────────────────


async def test_recall_no_persona_returns_empty():
    a = BaseAgent(role="x")
    rec = await a.recall("anything")
    assert rec.has_entries() is False
