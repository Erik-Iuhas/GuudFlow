"""Tests for incremental board persistence (Phase G-prime.0).

The runner attaches a board persister to the workspace at construction.
After every `post_signal`, the persister appends one JSON line to
`{run_root}/board.jsonl`. This is the crash-safe trail the resume
loader (G-prime.1) reads to reconstruct workspace state when a run
needs to pick up where it left off.

The end-of-run consolidated `board.json` written by `_persist_run`
in `worker/job_runner.py` is unchanged and still authoritative for
completed runs — board.jsonl is purely for in-flight + crashed
runs.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType
from core.tool_manifest import RoleRBAC
from core.workspace import Workspace


def _project() -> ProjectConfig:
    return ProjectConfig(
        project_id="p_persist",
        goal="x",
        agents=[
            AgentConfig(role="developer", system_prompt="x",
                         declared_local_tools=["fs:read"], model_tier="solid"),
        ],
        rbac={"developer": RoleRBAC(role="developer", allowed_scopes=[])},
        max_iterations=1,
    )


def _runner(tmp_path: Path) -> AgentRunner:
    return AgentRunner(
        project=_project(),
        run_id="run_persist_test",
        run_root=tmp_path / "runs" / "run_persist_test",
        llm=MockLLMClient([]),
    )


# ── Persister attachment ───────────────────────────────────────────────────


def test_runner_attaches_board_persister(tmp_path: Path):
    """A real runner constructed with a run_root has a persister wired."""
    runner = _runner(tmp_path)
    assert runner.workspace._board_persister is not None


def test_synthetic_workspace_has_no_persister():
    """A bare Workspace (no runner) should NOT auto-attach a persister
    — keeps unit tests for the workspace itself simple."""
    ws = Workspace(run_id="r", goal="x")
    assert ws._board_persister is None


# ── Per-signal append behaviour ────────────────────────────────────────────


def test_each_signal_appends_one_line(tmp_path: Path):
    runner = _runner(tmp_path)
    runner.workspace.post_signal(Signal(
        type=SignalType.STARTED, summary="agent A starting", from_agent="A",
    ))
    runner.workspace.post_signal(Signal(
        type=SignalType.GIT_COMMIT, summary="commit 1", from_agent="A",
    ))
    runner.workspace.post_signal(Signal(
        type=SignalType.COMPLETE, summary="agent A done", from_agent="A",
    ))

    board_path = runner.run_root / "board.jsonl"
    assert board_path.is_file()
    lines = board_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert [p["type"] for p in parsed] == ["STARTED", "GIT_COMMIT", "COMPLETE"]
    assert all(p["from_agent"] == "A" for p in parsed)


def test_signal_round_trip_preserves_fields(tmp_path: Path):
    """Each persisted line carries every field the resume loader will
    need: type, summary, detail, from_agent, iteration, seen_by, etc."""
    runner = _runner(tmp_path)
    sig = Signal(
        type=SignalType.PROTOCOL,
        summary="emit_commit by developer",
        detail='{"action":"COMMIT"}',
        from_agent="developer",
        priority="normal",
        iteration=2,
    )
    runner.workspace.post_signal(sig)

    line = (runner.run_root / "board.jsonl").read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["type"] == "PROTOCOL"
    assert parsed["summary"] == "emit_commit by developer"
    assert parsed["detail"] == '{"action":"COMMIT"}'
    assert parsed["from_agent"] == "developer"
    assert parsed["iteration"] == 2
    assert parsed["seen_by"] == []
    assert "id" in parsed
    assert "timestamp" in parsed


# ── Crash safety ──────────────────────────────────────────────────────────


def test_partial_trail_survives_simulated_crash(tmp_path: Path):
    """If the runner is interrupted after some signals but before
    `_persist_run` consolidates board.json, the resume loader should
    still see every signal that was posted."""
    runner = _runner(tmp_path)
    runner.workspace.post_signal(Signal(
        type=SignalType.STARTED, summary="A starting", from_agent="A",
    ))
    runner.workspace.post_signal(Signal(
        type=SignalType.GIT_COMMIT, summary="commit 1", from_agent="A",
    ))
    # Simulate "crash" — no end-of-run consolidation, no _persist_run call.
    # The jsonl trail is what the resume loader will read.
    board_path = runner.run_root / "board.jsonl"
    consolidated_path = runner.run_root / "board.json"

    assert board_path.is_file()
    assert not consolidated_path.exists()  # _persist_run never ran
    lines = board_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# ── Persister failure isolation ───────────────────────────────────────────


def test_persister_exception_does_not_break_post_signal(tmp_path: Path):
    """A buggy or full-disk persister must not take the run down with
    it. The workspace's existing 'subscriber exceptions are swallowed'
    contract extends to the persister."""
    ws = Workspace(run_id="r", goal="x")
    posted: list[Signal] = []

    def boom(_signal):
        raise RuntimeError("disk full")

    ws.set_board_persister(boom)
    # Subscribers also fire — verify they still receive the signal even
    # though the persister exploded.
    ws.subscribe(posted.append)

    sig = Signal(type=SignalType.STARTED, summary="A", from_agent="A")
    ws.post_signal(sig)  # must NOT raise

    assert ws.board == [sig]
    assert posted == [sig]


def test_set_persister_to_none_clears_it():
    ws = Workspace(run_id="r", goal="x")
    calls: list[Signal] = []
    ws.set_board_persister(calls.append)
    ws.post_signal(Signal(type=SignalType.STARTED, summary="A", from_agent="A"))
    assert len(calls) == 1

    ws.set_board_persister(None)
    ws.post_signal(Signal(type=SignalType.COMPLETE, summary="A", from_agent="A"))
    # Second signal didn't reach the (now-cleared) persister.
    assert len(calls) == 1
