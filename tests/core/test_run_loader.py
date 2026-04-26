"""Tests for core/run_loader.py — state restoration (Phase G-prime.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.run_loader import (
    derive_agent_states,
    load_persisted_board,
    restore_workspace,
)
from core.signals import Signal, SignalType
from core.workspace import AgentState, Artifact


@dataclass
class _StubProject:
    """Loader only needs `goal` (used as fallback) and `max_iterations`."""

    goal: str = "fallback goal"
    max_iterations: int = 5


# ── Helpers ────────────────────────────────────────────────────────────────


def _serialise_signal(sig: Signal) -> dict:
    """Match worker.job_runner.serialise_signal so the loader sees the
    same shape it'll see in production."""
    return {
        "id": str(sig.id),
        "type": sig.type.value,
        "summary": sig.summary,
        "detail": sig.detail,
        "from_agent": sig.from_agent,
        "from_user": sig.from_user,
        "priority": sig.priority,
        "iteration": sig.iteration,
        "seen_by": list(sig.seen_by),
        "timestamp": sig.timestamp.isoformat(),
    }


def _write_jsonl(run_root: Path, signals: list[Signal]) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(_serialise_signal(s)) for s in signals]
    (run_root / "board.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_meta(run_root: Path, **fields) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_root.name, "goal": "stored goal", "status": "running"}
    payload.update(fields)
    (run_root / "meta.json").write_text(json.dumps(payload), encoding="utf-8")


# ── load_persisted_board ───────────────────────────────────────────────────


def test_load_jsonl_preferred_when_both_files_exist(tmp_path: Path):
    """board.jsonl is the live trail; board.json may be stale (last
    end-of-run snapshot). When both exist, the loader prefers the
    JSONL — that's the contract."""
    rd = tmp_path / "run"
    sigs_jsonl = [
        Signal(type=SignalType.STARTED, summary="A", from_agent="A"),
        Signal(type=SignalType.COMPLETE, summary="A done", from_agent="A"),
    ]
    _write_jsonl(rd, sigs_jsonl)
    # Stale consolidated board with only one signal.
    (rd / "board.json").write_text(json.dumps([
        _serialise_signal(Signal(type=SignalType.STARTED, summary="old", from_agent="X")),
    ]), encoding="utf-8")

    loaded = load_persisted_board(rd)
    assert [s.type for s in loaded] == [SignalType.STARTED, SignalType.COMPLETE]
    assert all(s.from_agent == "A" for s in loaded)


def test_load_falls_back_to_consolidated_json(tmp_path: Path):
    """Older completed runs predate the JSONL trail. Loader must still
    work for those by reading board.json."""
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "board.json").write_text(json.dumps([
        _serialise_signal(Signal(type=SignalType.STARTED, summary="A", from_agent="A")),
    ]), encoding="utf-8")

    loaded = load_persisted_board(rd)
    assert len(loaded) == 1
    assert loaded[0].from_agent == "A"


def test_load_returns_empty_when_neither_file_exists(tmp_path: Path):
    rd = tmp_path / "run"
    rd.mkdir()
    assert load_persisted_board(rd) == []


def test_load_skips_malformed_jsonl_lines(tmp_path: Path):
    """A partially-corrupted JSONL shouldn't lose the whole trail —
    skip the broken line, return the rest."""
    rd = tmp_path / "run"
    rd.mkdir()
    good = _serialise_signal(Signal(type=SignalType.STARTED, summary="A", from_agent="A"))
    (rd / "board.jsonl").write_text(
        json.dumps(good) + "\n" + "{not json\n" + json.dumps(good) + "\n",
        encoding="utf-8",
    )
    loaded = load_persisted_board(rd)
    assert len(loaded) == 2


# ── derive_agent_states ───────────────────────────────────────────────────


def test_derive_states_from_signal_lifecycle():
    sigs = [
        Signal(type=SignalType.STARTED, summary="A", from_agent="A"),
        Signal(type=SignalType.STARTED, summary="B", from_agent="B"),
        Signal(type=SignalType.COMPLETE, summary="A done", from_agent="A"),
    ]
    states = derive_agent_states(sigs)
    assert states["A"] is AgentState.COMPLETE
    assert states["B"] is AgentState.RUNNING


def test_derive_states_failed_overrides_started():
    sigs = [
        Signal(type=SignalType.STARTED, summary="A", from_agent="A"),
        Signal(type=SignalType.FAILED, summary="A crashed", from_agent="A"),
    ]
    states = derive_agent_states(sigs)
    assert states["A"] is AgentState.FAILED


def test_derive_states_human_needed():
    sigs = [
        Signal(type=SignalType.STARTED, summary="A", from_agent="A"),
        Signal(type=SignalType.HUMAN_NEEDED, summary="?", from_agent="A"),
    ]
    states = derive_agent_states(sigs)
    assert states["A"] is AgentState.WAITING_FOR_HUMAN


def test_derive_states_ignores_non_lifecycle_signals():
    """GIT_COMMIT, PROTOCOL, and other non-lifecycle types don't
    affect agent_states even with a populated from_agent. Only
    STARTED/COMPLETE/FAILED/HUMAN_NEEDED move the state machine."""
    sigs = [
        Signal(type=SignalType.GIT_COMMIT, summary="commit", from_agent="A"),
        Signal(type=SignalType.PROTOCOL, summary="emit", from_agent="A"),
    ]
    states = derive_agent_states(sigs)
    assert states == {}


def test_derive_states_anonymous_signals_skipped():
    """A signal with no from_agent never contributes to state."""
    sigs = [Signal(type=SignalType.GIT_COMMIT, summary="x")]
    states = derive_agent_states(sigs)
    assert states == {}


# ── restore_workspace ─────────────────────────────────────────────────────


def test_restore_empty_run_yields_fresh_equivalent_workspace(tmp_path: Path):
    rd = tmp_path / "run_empty"
    rd.mkdir()
    project = _StubProject(goal="hi")

    ws = restore_workspace(rd, project)
    assert ws.goal == "hi"  # falls back to project goal
    assert ws.board == []
    assert ws.completed_agents == []
    assert ws.iteration == 0
    assert ws.artifacts == {}


def test_restore_uses_meta_goal_over_project_goal(tmp_path: Path):
    """The run was launched against a specific goal; the project's
    current goal might have been edited since. Recorded > current."""
    rd = tmp_path / "run"
    _write_meta(rd, goal="recorded goal")
    project = _StubProject(goal="newer goal")

    ws = restore_workspace(rd, project)
    assert ws.goal == "recorded goal"


def test_restore_full_state_from_jsonl(tmp_path: Path):
    """End-to-end: a typical run with two agents, A complete + B failed."""
    rd = tmp_path / "run"
    sigs = [
        Signal(type=SignalType.STARTED, summary="A", from_agent="A", iteration=1),
        Signal(type=SignalType.GIT_COMMIT, summary="commit 1",
               from_agent="A", iteration=1),
        Signal(type=SignalType.COMPLETE, summary="A done",
               from_agent="A", iteration=1),
        Signal(type=SignalType.STARTED, summary="B", from_agent="B", iteration=2),
        Signal(type=SignalType.FAILED, summary="B crashed",
               from_agent="B", iteration=2),
    ]
    _write_jsonl(rd, sigs)
    _write_meta(rd, goal="actual goal")

    ws = restore_workspace(rd, _StubProject())

    assert ws.goal == "actual goal"
    assert len(ws.board) == 5
    assert ws.completed_agents == ["A"]
    assert ws.iteration == 2
    assert ws.agent_states["A"] is AgentState.COMPLETE
    assert ws.agent_states["B"] is AgentState.FAILED


def test_restore_loads_artifacts(tmp_path: Path):
    rd = tmp_path / "run"
    rd.mkdir()
    (rd / "artifacts.json").write_text(json.dumps({
        "developer": {
            "agent_role": "developer",
            "type": "text",
            "content": "def greet():\n    return 'hi'\n",
            "iteration": 1,
            "timestamp": "2026-04-26T12:00:00+00:00",
        },
    }), encoding="utf-8")

    ws = restore_workspace(rd, _StubProject())
    assert "developer" in ws.artifacts
    assert isinstance(ws.artifacts["developer"], Artifact)
    assert "def greet" in ws.artifacts["developer"].content
    assert ws.artifacts["developer"].iteration == 1


def test_restore_user_board_signals_routed_correctly(tmp_path: Path):
    """USER_COMMENT signals should appear in BOTH ws.board AND
    ws.user_board after restore."""
    from core.workspace import USER_BOARD_TYPES
    # Use a user-board type if one exists in USER_BOARD_TYPES.
    user_type = next(iter(USER_BOARD_TYPES))
    rd = tmp_path / "run"
    sigs = [Signal(type=user_type, summary="hi from user")]
    _write_jsonl(rd, sigs)

    ws = restore_workspace(rd, _StubProject())
    assert len(ws.board) == 1
    assert len(ws.user_board) == 1
    assert ws.board[0].type is user_type


def test_round_trip_persistence_to_restoration(tmp_path: Path):
    """Persist via the same path the runner uses (workspace board
    persister + serialise_signal), then restore via load_persisted_board.
    The signal list must round-trip cleanly."""
    from core.workspace import Workspace
    from worker.job_runner import serialise_signal

    rd = tmp_path / "run"
    rd.mkdir()
    board_path = rd / "board.jsonl"

    def persister(sig: Signal) -> None:
        with open(board_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(serialise_signal(sig)) + "\n")

    ws = Workspace(run_id="r", goal="x")
    ws.set_board_persister(persister)
    ws.post_signal(Signal(type=SignalType.STARTED, summary="A", from_agent="A"))
    ws.post_signal(Signal(type=SignalType.COMPLETE, summary="A done",
                           from_agent="A", iteration=1))

    loaded = load_persisted_board(rd)
    assert [s.type for s in loaded] == [SignalType.STARTED, SignalType.COMPLETE]
    assert [s.summary for s in loaded] == ["A", "A done"]
    assert loaded[1].iteration == 1
