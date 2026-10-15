"""Tests for core.harness.mcp_tools (board-facing MCP tools)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.mcp_tools import tools
from core.harness.tool_context import RunToolContext
from core.signals import Signal, SignalType
from core.workspace import HumanRequest, Workspace

ROLE = "builder"
IDENTITY = McpIdentity(run_id="r", role=ROLE, tools=frozenset())


class Env:
    """A tool context over a real workspace and a temp clone, plus a fake installer."""

    def __init__(self, tmp_path: Path, settings: dict[str, Any] | None = None,
                 install_result: tuple[int, str] = (0, "ok")) -> None:
        self.workspace = Workspace(run_id="r", goal="g")
        self.clone = tmp_path / "clone"
        subprocess.run(["git", "init", "-q", str(self.clone)], check=True)
        self.calls: list[list[str]] = []
        self.install_result = install_result

        async def installer(argv: list[str]) -> tuple[int, str]:
            self.calls.append(argv)
            return self.install_result

        self.ctx = RunToolContext(run_id="r", workspace=self.workspace, run_root=tmp_path,
                                  clone_for=lambda role: self.clone,
                                  sandbox_host_for=lambda role: "127.0.0.1",
                                  settings=settings or {})
        self.by_name: dict[str, McpTool] = {t.name: t for t in tools(self.ctx, installer=installer)}

    async def call(self, tool: str, /, **args: Any) -> ToolResult:
        return await self.by_name[tool].handler(IDENTITY, args)

    def reply_to(self, wanted: SignalType, make: Callable[[Signal], Signal]) -> None:
        """Act like the HTTP route: answer the next `wanted` signal once."""
        def on_signal(sig: Signal) -> None:
            if sig.type is not wanted:
                return
            unsubscribe()
            asyncio.get_running_loop().call_soon(self.workspace.post_signal, make(sig))
        unsubscribe = self.workspace.subscribe(on_signal)

    def signals(self, kind: SignalType) -> list[Signal]:
        return self.workspace.signals_of_type(kind)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def _grant(env: Env, decision: SignalType) -> None:
    def make(sig: Signal) -> Signal:
        approval_id = (sig.detail or "").split(";")[0].split("=")[1]
        env.workspace.record_approval(
            approval_id, "granted" if decision is SignalType.APPROVAL_GRANTED else "denied")
        return Signal(type=decision, summary="decided", from_agent="user",
                      detail=f"approval_id={approval_id}; kind=package_install")
    env.reply_to(SignalType.APPROVAL_NEEDED, make)


# ── read_board ──────────────────────────────────────────────────────────────
async def test_read_board_lists_newest_last_and_limits(env: Env) -> None:
    for i in range(5):
        env.ctx.post(SignalType.THINKING, "a", f"step {i}")
    out = await env.call("read_board", limit=2)
    assert out.text.splitlines() == ["[THINKING] a: step 3", "[THINKING] a: step 4"]


async def test_read_board_filters_by_type_and_names_user(env: Env) -> None:
    env.ctx.post(SignalType.THINKING, "a", "thinking")
    env.workspace.post_signal(Signal(type=SignalType.USER_COMMENT, summary="hello"))
    out = await env.call("read_board", types=["user_comment"])
    assert out.text == "[USER_COMMENT] user: hello"


async def test_read_board_never_exposes_sensitive_detail(env: Env) -> None:
    env.ctx.post(SignalType.CAPABILITIES, "a", "caps", {"secret": "CAPS-DETAIL"})
    env.ctx.post(SignalType.APPROVAL_NEEDED, "a", "needs approval", "context=APPROVAL-DETAIL")
    env.ctx.post(SignalType.APPROVAL_GRANTED, "a", "granted", "approval_id=APPROVAL-DETAIL")
    out = await env.call("read_board")
    assert "CAPS-DETAIL" not in out.text and "APPROVAL-DETAIL" not in out.text
    assert "[CAPABILITIES] a: caps" in out.text


async def test_read_board_caps_limit_and_rejects_bad_args(env: Env) -> None:
    for i in range(120):
        env.ctx.post(SignalType.THINKING, "a", str(i))
    assert len((await env.call("read_board", limit=500)).text.splitlines()) == 100
    for bad in ({"limit": 0}, {"limit": "x"}, {"types": "COMPLETE"}, {"types": ["NOPE"]}):
        assert (await env.call("read_board", **bad)).is_error


async def test_read_board_empty(env: Env) -> None:
    assert not (await env.call("read_board")).is_error


# ── report_progress ─────────────────────────────────────────────────────────
async def test_report_progress_posts_thinking_from_identity_role(env: Env) -> None:
    out = await env.call("report_progress", message="  " + "x" * 400 + "  ", role="spoofed")
    assert out.text == "noted"
    (sig,) = env.signals(SignalType.THINKING)
    assert sig.from_agent == ROLE and sig.summary == "x" * 300


async def test_report_progress_rejects_empty(env: Env) -> None:
    assert (await env.call("report_progress", message="  ")).is_error
    assert (await env.call("report_progress")).is_error


# ── ask_human ───────────────────────────────────────────────────────────────
async def test_ask_human_answered(env: Env) -> None:
    seen: list[HumanRequest | None] = []

    def make(sig: Signal) -> Signal:
        seen.append(env.workspace.pending_human)
        qid = json.loads(sig.detail or "")["question_id"]
        return Signal(type=SignalType.HUMAN_RESPONDED, summary="reply",
                      detail=json.dumps({"question_id": qid, "answer": "use blue"}))
    env.reply_to(SignalType.HUMAN_NEEDED, make)
    out = await env.call("ask_human", question="Which colour?")
    assert out.text == "use blue" and not out.is_error
    assert seen[0] is not None and seen[0].from_agent == ROLE
    assert seen[0].question == "Which colour?"
    assert env.workspace.pending_human is None
    (needed,) = env.signals(SignalType.HUMAN_NEEDED)
    assert json.loads(needed.detail or "")["question"] == "Which colour?"


async def test_ask_human_ignores_other_question_ids(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.harness.mcp_tools as mod
    monkeypatch.setattr(mod, "MIN_TIMEOUT_S", 0.05)
    def make(sig: Signal) -> Signal:
        return Signal(type=SignalType.HUMAN_RESPONDED, summary="x",
                      detail=json.dumps({"question_id": "other", "answer": "wrong"}))
    env.reply_to(SignalType.HUMAN_NEEDED, make)
    out = await env.call("ask_human", question="q", timeout_s=0.01)  # clamped up to the minimum
    assert out.text.startswith("No answer") and not out.is_error


async def test_ask_human_timeout_is_not_an_error(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.harness.mcp_tools as mod
    monkeypatch.setattr(mod, "MIN_TIMEOUT_S", 0.05)
    out = await env.call("ask_human", question="anyone?", timeout_s=0)
    assert not out.is_error
    assert out.text == ("No answer from the human within 0.05 seconds; continue with your best "
                        "judgement and say so in your summary.")
    assert env.workspace.pending_human is None


async def test_ask_human_refused_while_another_pending(env: Env) -> None:
    existing = HumanRequest(from_agent="other", question="first")
    env.workspace.pending_human = existing
    out = await env.call("ask_human", question="second")
    assert out.is_error
    assert env.workspace.pending_human is existing  # untouched
    assert not env.signals(SignalType.HUMAN_NEEDED)


async def test_ask_human_clears_pending_when_cancelled(env: Env) -> None:
    task = asyncio.create_task(env.call("ask_human", question="q"))
    await asyncio.sleep(0.01)
    assert env.workspace.pending_human is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.workspace.pending_human is None


async def test_ask_human_validates(env: Env) -> None:
    assert (await env.call("ask_human", question=" ")).is_error
    assert (await env.call("ask_human", question="q", timeout_s="soon")).is_error


# ── request_visual_feedback ─────────────────────────────────────────────────
async def test_visual_feedback_answered(env: Env) -> None:
    (env.clone / "shots").mkdir()
    (env.clone / "shots" / "home.png").write_bytes(b"\x89PNG")

    def make(sig: Signal) -> Signal:
        rid = json.loads(sig.detail or "")["request_id"]
        return Signal(type=SignalType.USER_VISUAL_FEEDBACK, summary="fb",
                      detail=json.dumps({"request_id": rid, "feedback": "make it bigger"}))
    env.reply_to(SignalType.SCREENSHOT_READY, make)
    out = await env.call("request_visual_feedback", path="shots/home.png", question="Good?")
    assert out.text == "make it bigger" and not out.is_error
    (ready,) = env.signals(SignalType.SCREENSHOT_READY)
    detail = json.loads(ready.detail or "")
    assert detail["path"] == "shots/home.png" and detail["question"] == "Good?"
    assert ready.from_agent == ROLE


async def test_visual_feedback_timeout_is_not_an_error(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.harness.mcp_tools as mod
    monkeypatch.setattr(mod, "MIN_TIMEOUT_S", 0.05)
    (env.clone / "a.webp").write_bytes(b"x")
    out = await env.call("request_visual_feedback", path="a.webp", question="ok?", timeout_s=0)
    assert not out.is_error and out.text.startswith("No feedback")


async def test_visual_feedback_rejects_bad_paths(env: Env, tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    (env.clone / "link.png").symlink_to(outside)
    (env.clone / "notes.txt").write_text("hi")
    (env.clone / "real.png").write_bytes(b"x")
    (env.clone / "dir.png").mkdir()
    for bad in ("../outside.png", str(outside), "link.png", "notes.txt", "missing.png",
                "dir.png", "sub/../../outside.png"):
        out = await env.call("request_visual_feedback", path=bad, question="q")
        assert out.is_error, bad
    assert not env.signals(SignalType.SCREENSHOT_READY)
    ok = asyncio.create_task(env.call("request_visual_feedback", path="real.png", question="q",
                                      timeout_s=10))
    await asyncio.sleep(0.01)
    assert env.signals(SignalType.SCREENSHOT_READY)
    ok.cancel()


# ── request_package ─────────────────────────────────────────────────────────
PIP_ARGV = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
            "--isolated", "--only-binary=:all:", "--target"]


async def test_package_allow_listed_pip(tmp_path: Path) -> None:
    env = Env(tmp_path, settings={"package_allow": {"pip": ["requests==2.31.0"]}})
    out = await env.call("request_package", manager="pip", name="requests", version="2.31.0")
    assert not out.is_error
    assert "PYTHONPATH=.guudflow/site-packages" in out.text
    # Installs go to the host-only tool dir, never into the agent's worktree (O8 review).
    assert env.calls == [PIP_ARGV + [str(env.clone / ".guudflow" / "site-packages"),
                                     "requests==2.31.0"]]
    assert "--isolated" in env.calls[0]
    kinds = [s.type for s in env.workspace.board]
    assert kinds == [SignalType.PACKAGE_REQUEST, SignalType.PACKAGE_APPROVED]
    assert not env.signals(SignalType.APPROVAL_NEEDED)


async def test_package_allow_listed_npm_and_scoped(tmp_path: Path) -> None:
    env = Env(tmp_path, settings={"package_allow": {"npm": ["left-pad@1.3.0", "@types/node@*"]}})
    out = await env.call("request_package", manager="npm", name="left-pad", version="1.3.0")
    assert not out.is_error and "node_modules" in out.text
    out = await env.call("request_package", manager="npm", name="@types/node", version="20.1.0")
    assert not out.is_error
    argv = env.calls[0]
    assert argv[:5] == ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    assert "--registry=https://registry.npmjs.org/" in argv and "left-pad@1.3.0" in argv
    # npm runs in a fresh temp prefix with an empty userconfig — never in the worktree,
    # so an agent-written package.json or .npmrc cannot steer it.
    prefix = Path(argv[argv.index("--prefix") + 1])
    assert not prefix.is_relative_to(env.clone)
    assert argv[argv.index("--userconfig") + 1].startswith(str(prefix))
    assert "@types/node@20.1.0" in env.calls[1]


async def test_package_wildcard_version_and_name_normalisation(tmp_path: Path) -> None:
    env = Env(tmp_path, settings={"package_allow": {"pip": ["Typing_Extensions==*"]}})
    out = await env.call("request_package", manager="pip", name="typing-extensions",
                         version="4.9.0")
    assert not out.is_error and len(env.calls) == 1
    assert not env.signals(SignalType.APPROVAL_NEEDED)


async def test_package_wrong_version_not_allow_listed_needs_approval(tmp_path: Path) -> None:
    env = Env(tmp_path, settings={"package_allow": {"pip": ["requests==2.31.0"]}})
    _grant(env, SignalType.APPROVAL_DENIED)
    out = await env.call("request_package", manager="pip", name="requests", version="2.0.0")
    assert out.is_error and not env.calls
    assert env.signals(SignalType.APPROVAL_NEEDED)


async def test_package_approval_granted_installs(env: Env) -> None:
    _grant(env, SignalType.APPROVAL_GRANTED)
    out = await env.call("request_package", manager="pip", name="rich", version="13.7.1")
    assert not out.is_error and len(env.calls) == 1
    (needed,) = env.signals(SignalType.APPROVAL_NEEDED)
    assert "kind=package_install" in (needed.detail or "")
    request = env.workspace.pending_approvals[0]
    assert request.kind == "package_install" and request.decision == "granted"
    assert request.context == {"role": ROLE, "manager": "pip", "name": "rich",
                               "version": "13.7.1"}
    kinds = [s.type for s in env.workspace.board]
    assert kinds.index(SignalType.PACKAGE_REQUEST) < kinds.index(SignalType.APPROVAL_NEEDED) \
        < kinds.index(SignalType.PACKAGE_APPROVED)


async def test_package_approval_denied(env: Env) -> None:
    _grant(env, SignalType.APPROVAL_DENIED)
    out = await env.call("request_package", manager="npm", name="lodash", version="4.17.21")
    assert out.is_error and not env.calls
    assert env.signals(SignalType.PACKAGE_REJECTED)
    assert not env.signals(SignalType.PACKAGE_APPROVED)


async def test_package_approval_timeout_denies(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.harness.mcp_tools as mod
    real = mod._ask_approval

    async def quick(ctx: RunToolContext, role: str, summary: str, context: dict[str, Any],
                    timeout: float = 0.05) -> bool:
        return await real(ctx, role, summary, context, timeout=0.05)
    monkeypatch.setattr(mod, "_ask_approval", quick)
    out = await env.call("request_package", manager="pip", name="rich", version="13.7.1")
    assert out.is_error and not env.calls
    assert env.workspace.pending_approvals[0].decision == "denied"


@pytest.mark.parametrize("args", [
    {"manager": "gem", "name": "rails", "version": "7.0.0"},
    {"manager": "pip", "name": "req uests", "version": "1.0"},
    {"manager": "pip", "name": "-rf", "version": "1.0"},
    {"manager": "pip", "name": "a;rm -rf /", "version": "1.0"},
    {"manager": "pip", "name": "@scope/pkg", "version": "1.0"},
    {"manager": "npm", "name": "@/pkg", "version": "1.0.0"},
    {"manager": "npm", "name": "a/b", "version": "1.0.0"},
    {"manager": "pip", "name": "requests", "version": ">=2.0"},
    {"manager": "pip", "name": "requests", "version": "^2.0.0"},
    {"manager": "npm", "name": "lodash", "version": "latest"},
    {"manager": "npm", "name": "lodash", "version": "1.0 || 2.0"},
    {"manager": "npm", "name": "lodash", "version": "*"},
    {"manager": "pip", "name": "requests", "version": ""},
    {"manager": "pip", "name": "requests"},
    {"manager": "pip", "name": 3, "version": "1.0"},
])
async def test_package_rejects_invalid_arguments(tmp_path: Path, args: dict[str, Any]) -> None:
    env = Env(tmp_path, settings={"package_allow": {"pip": ["*==*"], "npm": ["*@*"]}})
    out = await env.call("request_package", **args)
    assert out.is_error and not env.calls
    assert not env.signals(SignalType.PACKAGE_REQUEST)


async def test_package_installer_failure_is_an_error(tmp_path: Path) -> None:
    env = Env(tmp_path, settings={"package_allow": {"pip": ["rich==*"]}},
              install_result=(1, "E" * 3000))
    out = await env.call("request_package", manager="pip", name="rich", version="1.0.0")
    assert out.is_error and "exit 1" in out.text
    assert len(out.text) < 2200


async def test_package_installer_exception_is_an_error(tmp_path: Path) -> None:
    env = Env(tmp_path, settings={"package_allow": {"pip": ["rich==*"]}})

    async def boom(argv: list[str]) -> tuple[int, str]:
        raise RuntimeError("no network")
    by_name = {t.name: t for t in tools(env.ctx, installer=boom)}
    out = await by_name["request_package"].handler(
        IDENTITY, {"manager": "pip", "name": "rich", "version": "1.0.0"})
    assert out.is_error and "no network" in out.text


async def test_package_ignores_agent_package_json_and_npmrc(tmp_path: Path) -> None:
    env = Env(tmp_path, settings={"package_allow": {"npm": ["left-pad@1.3.0"]}})
    (env.clone / "package.json").write_text('{"dependencies": {"evil": "1.0.0"}}')
    (env.clone / ".npmrc").write_text("registry=http://169.254.169.254/\n")
    await env.call("request_package", manager="npm", name="left-pad", version="1.3.0")
    argv = env.calls[0]
    assert str(env.clone) not in " ".join(argv)


async def test_default_installer_runs_without_shell(tmp_path: Path) -> None:
    from core.harness.mcp_tools import _run_installer
    code, out = await _run_installer([sys.executable, "-c", "print('hi; $HOME')"])
    assert code == 0 and out.strip() == "hi; $HOME"
    code, _ = await _run_installer(["definitely-not-a-real-binary-xyz"])
    assert code == 127


def test_tool_names_and_schemas(env: Env) -> None:
    assert list(env.by_name) == ["read_board", "report_progress", "ask_human",
                                 "request_visual_feedback", "request_package",
                                 "sync_dependencies"]
    for tool in env.by_name.values():
        schema = tool.schema_for(IDENTITY)
        assert schema["type"] == "object" and "role" not in schema["properties"]


# ── sync_dependencies (plan §16) ────────────────────────────────────────────
def _deps_env(tmp_path: Path, allow: dict[str, list[str]] | None = None) -> Env:
    from core.deps import ProjectDeps

    e = Env(tmp_path, settings={"package_allow": allow or {}})
    e.ctx.deps = ProjectDeps(tmp_path / "deps")
    e.ctx.deps_mount = str(tmp_path / "deps")

    async def installer(argv: list[str]) -> tuple[int, str]:
        e.calls.append(argv)
        prefix = Path(argv[argv.index("--prefix") + 1]) if "--prefix" in argv else None
        if prefix is not None:
            (prefix / "node_modules" / "left-pad").mkdir(parents=True)
        return e.install_result
    e.by_name = {t.name: t for t in tools(e.ctx, installer=installer)}
    return e


async def test_sync_dependencies_allow_listed_installs_once_and_links(tmp_path: Path) -> None:
    e = _deps_env(tmp_path, {"npm": ["left-pad@1.3.0"]})
    (e.clone / "package.json").write_text('{"dependencies": {"left-pad": "1.3.0"}}')
    out = await e.call("sync_dependencies", manager="npm")
    assert not out.is_error, out.text
    assert len(e.calls) == 1 and "--ignore-scripts" in e.calls[0]
    assert (e.clone / "node_modules").is_symlink()
    assert (e.clone / "node_modules" / "left-pad").is_dir()
    env = e.ctx.deps.current("npm")
    assert env and env.packages[0].added_by == ROLE and env.packages[0].approved_by == "allow-list"
    again = await e.call("sync_dependencies", manager="npm")
    assert "Already in sync" in again.text and len(e.calls) == 1
    assert e.signals(SignalType.PACKAGE_APPROVED) and not e.signals(SignalType.APPROVAL_NEEDED)


async def test_sync_dependencies_needs_one_approval_for_unlisted(tmp_path: Path) -> None:
    e = _deps_env(tmp_path)
    (e.clone / "requirements.txt").write_text("rich==13.7.1\nattrs==23.2.0  # pinned\n")
    _grant(e, SignalType.APPROVAL_DENIED)
    out = await e.call("sync_dependencies", manager="pip")
    assert out.is_error and "attrs==23.2.0" in out.text and not e.calls
    assert len(e.signals(SignalType.APPROVAL_NEEDED)) == 1 and e.signals(SignalType.PACKAGE_REJECTED)
    assert e.ctx.deps.current("pip") is None


async def test_sync_dependencies_refuses_ranges(tmp_path: Path) -> None:
    e = _deps_env(tmp_path)
    (e.clone / "package.json").write_text('{"dependencies": {"a": "^1.0.0", "b": "git+https://x"}}')
    out = await e.call("sync_dependencies", manager="npm")
    assert out.is_error and "a:" in out.text and "b:" in out.text and not e.calls
