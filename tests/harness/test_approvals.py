"""Tests for core.harness.approvals.PermissionGate."""

from __future__ import annotations

import asyncio

from core.harness.approvals import PermissionGate
from core.signals import Signal, SignalType
from core.workspace import Workspace


def _ws() -> Workspace:
    return Workspace(run_id="r", goal="g")


def _answer(ws: Workspace, decision: str, scope: str = "once", note: str | None = None) -> None:
    """Act like the HTTP route: answer the next APPROVAL_NEEDED (once)."""
    def on_signal(sig: Signal) -> None:
        if sig.type is not SignalType.APPROVAL_NEEDED:
            return
        unsubscribe()
        approval_id = (sig.detail or "").split(";")[0].split("=")[1]
        word = "granted" if decision == "grant" else "denied"
        ws.record_approval(approval_id, word, note=note)
        kind = SignalType.APPROVAL_GRANTED if decision == "grant" else SignalType.APPROVAL_DENIED
        detail = f"approval_id={approval_id}; kind=tool_permission"
        if decision == "grant":
            detail += f"; scope={scope}"
        asyncio.get_running_loop().call_soon(
            ws.post_signal, Signal(type=kind, summary=word, from_agent="user", detail=detail))
    unsubscribe = ws.subscribe(on_signal)


ASK = {"id": "per_1", "permission": "bash", "patterns": ["touch a.txt"], "always": ["touch *"]}


async def test_human_grant_once() -> None:
    ws = _ws()
    _answer(ws, "grant")
    assert await PermissionGate(ws).decide("builder", ASK) == "once"
    assert ws.pending_approvals[0].kind == "tool_permission"
    assert ws.pending_approvals[0].context["patterns"] == ["touch a.txt"]


async def test_human_deny() -> None:
    ws = _ws()
    _answer(ws, "deny")
    assert await PermissionGate(ws).decide("builder", ASK) == "reject"


async def test_run_scope_becomes_sticky_for_that_role_only() -> None:
    ws = _ws()
    _answer(ws, "grant", scope="run")
    gate = PermissionGate(ws)
    assert await gate.decide("builder", ASK) == "always"
    needed_before = sum(s.type is SignalType.APPROVAL_NEEDED for s in ws.board)
    second = {**ASK, "patterns": ["touch b.txt"]}
    assert await gate.decide("builder", second) == "once"
    assert sum(s.type is SignalType.APPROVAL_NEEDED for s in ws.board) == needed_before
    assert any("always, this run" in s.summary for s in ws.board)
    # Another role still has to ask.
    _answer(ws, "deny")
    assert await gate.decide("tester", second) == "reject"


async def test_allow_list_auto_approves() -> None:
    ws = _ws()
    gate = PermissionGate(ws, allow_list={"bash": ["touch *"]})
    assert await gate.decide("builder", ASK) == "once"
    assert not ws.pending_approvals


async def test_timeout_rejects_and_marks_denied() -> None:
    ws = _ws()
    gate = PermissionGate(ws, timeout=0.05)
    assert await gate.decide("builder", ASK) == "reject"
    assert ws.pending_approvals[0].decision == "denied"


async def test_the_humans_reason_reaches_the_asking_role() -> None:
    ws = _ws()
    _answer(ws, "deny", note="Use bun test instead of touching files in the repo root.")
    assert await PermissionGate(ws).decide("builder", ASK) == "reject"
    comments = [s for s in ws.board if s.type is SignalType.USER_COMMENT_PRIORITY]
    assert len(comments) == 1
    assert "Use bun test instead" in comments[0].summary and "refused" in comments[0].summary
    assert '"to": "builder"' in (comments[0].detail or "")


async def test_no_note_no_comment() -> None:
    ws = _ws()
    _answer(ws, "grant")
    await PermissionGate(ws).decide("builder", ASK)
    assert not [s for s in ws.board if s.type is SignalType.USER_COMMENT_PRIORITY]
