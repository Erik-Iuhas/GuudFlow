"""A turn whose shell could not find the test runner cannot claim tests passed (findings F15)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from core.harness.opencode_runner import drive_turn, missing_programs
from core.harness.results import AcceptanceCheck, AgentResult, unverified_claims


def test_not_found_messages_from_common_shells() -> None:
    assert missing_programs("bash: bun: command not found") == {"bun"}
    assert missing_programs("/bin/sh: 1: bun: not found\n") == {"bun"}
    assert missing_programs("zsh: command not found: pytest") == {"pytest"}
    assert missing_programs("12 pass\n0 fail") == set()


def _result() -> AgentResult:
    return AgentResult(status="done", summary="Implemented the counter and its tests", acceptance=[
        AcceptanceCheck(item="Counter caps at 99", met=True, evidence="tests/counter.test.js verifies the cap"),
        AcceptanceCheck(item="web/src/counter.js exists", met=True, evidence="created the module file"),
        AcceptanceCheck(item="Cap message shown", met=False, evidence=""),
    ])


def test_test_backed_claims_become_unmet_with_the_reason() -> None:
    result, changed = unverified_claims(_result(), {"bun"})
    assert changed == ["Counter caps at 99"]
    first, second, third = result.acceptance
    assert not first.met and "bun was not available" in first.evidence and "verifies the cap" in first.evidence
    assert second.met and not third.met                       # not about tests / already unmet


def test_nothing_changes_when_the_missing_program_is_not_a_test_runner() -> None:
    result, changed = unverified_claims(_result(), {"tree"})
    assert changed == [] and result.acceptance[0].met


class _Client:
    async def events(self) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "server.connected", "properties": {}}
        yield {"type": "message.part.updated", "properties": {"part": {
            "id": "p1", "type": "tool", "tool": "bash",
            "state": {"status": "completed", "output": "bash: bun: command not found\n"}}}}
        yield {"type": "session.idle", "properties": {"sessionID": "s"}}
        await asyncio.sleep(3600)

    async def prompt_async(self, *a: Any, **k: Any) -> None:
        return None

    async def abort(self, session_id: str) -> None:
        return None


async def test_the_turn_records_programs_its_shell_could_not_find() -> None:
    stats = await drive_turn(_Client(), "s", "hi", timeout=5, stall=5)  # type: ignore[arg-type]
    assert stats.missing_programs == {"bun"}
