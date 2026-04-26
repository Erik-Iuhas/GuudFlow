"""Run state restoration loader (Phase G-prime.1).

Reads the on-disk persisted state of a run and reconstructs a
`Workspace` ready to drive a resumed scheduler. The runner attaches
the result via its constructor's `restored_workspace=` parameter
(G-prime.3).

Reads in priority order:

1. `board.jsonl` — the incremental crash-safe trail (G-prime.0)
2. `board.json` — the end-of-run consolidated snapshot
3. `meta.json` — for the run's `goal` (project might have a different
   goal now; the run's recorded one wins)
4. `artifacts.json` — completed agents' outputs

If both `board.jsonl` and `board.json` exist, `board.jsonl` wins
(it's the live trail; `board.json` may be stale).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from core.signals import Priority, Signal, SignalType
from core.workspace import (
    AgentState,
    Artifact,
    USER_BOARD_TYPES,
    Workspace,
)


# ── Public API ─────────────────────────────────────────────────────────────


def restore_workspace(run_root: Path, project) -> Workspace:
    """Reconstruct a Workspace from a run's persisted state.

    `project` is the live `ProjectConfig` — used for `max_iterations`.
    The run's recorded goal (from meta.json) overrides the project's
    current goal, since the run was launched against the goal at that
    time.

    Returns a Workspace whose `board`, `user_board`, `agent_states`,
    `iteration`, `completed_agents`, and `artifacts` reflect what was
    persisted. The persister hooks are NOT attached here — the runner
    attaches them when it accepts the restored workspace.
    """
    run_root = Path(run_root)

    goal = _load_goal(run_root, fallback=project.goal)
    ws = Workspace(
        run_id=run_root.name,
        goal=goal,
        max_iterations=project.max_iterations,
    )

    signals = load_persisted_board(run_root)
    for sig in signals:
        ws.board.append(sig)
        if sig.type in USER_BOARD_TYPES:
            ws.user_board.append(sig)

    ws.completed_agents = [
        sig.from_agent for sig in signals
        if sig.type is SignalType.COMPLETE and sig.from_agent
    ]
    ws.agent_states = derive_agent_states(signals)
    ws.iteration = max((sig.iteration for sig in signals), default=0)
    ws.artifacts = _load_artifacts(run_root)

    return ws


def load_persisted_board(run_root: Path) -> list[Signal]:
    """Load signals from board.jsonl (preferred) or board.json. Returns
    [] if neither exists. Tolerates malformed lines (skips them) so a
    partially-corrupted JSONL doesn't lose the whole trail."""
    run_root = Path(run_root)
    jsonl_path = run_root / "board.jsonl"
    json_path = run_root / "board.json"

    if jsonl_path.is_file():
        signals: list[Signal] = []
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                signals.append(_deserialise_signal(json.loads(line)))
            except (json.JSONDecodeError, ValueError, KeyError):
                # Skip malformed lines; resume can still run on partial
                # state. The end-of-run consolidator wouldn't have
                # written corrupt data.
                continue
        return signals

    if json_path.is_file():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        signals = []
        for raw in payload:
            try:
                signals.append(_deserialise_signal(raw))
            except (ValueError, KeyError):
                continue
        return signals

    return []


def derive_agent_states(signals: list[Signal]) -> dict[str, AgentState]:
    """Map each agent's latest lifecycle signal to its AgentState.

    Lifecycle signal types (in priority order — later events override
    earlier): STARTED → RUNNING, FAILED → FAILED, COMPLETE → COMPLETE,
    HUMAN_NEEDED → WAITING_FOR_HUMAN.
    """
    state: dict[str, AgentState] = {}
    for sig in signals:
        role = sig.from_agent
        if not role:
            continue
        if sig.type is SignalType.STARTED:
            state[role] = AgentState.RUNNING
        elif sig.type is SignalType.COMPLETE:
            state[role] = AgentState.COMPLETE
        elif sig.type is SignalType.FAILED:
            state[role] = AgentState.FAILED
        elif sig.type is SignalType.HUMAN_NEEDED:
            state[role] = AgentState.WAITING_FOR_HUMAN
    return state


# ── Internals ──────────────────────────────────────────────────────────────


def _load_goal(run_root: Path, *, fallback: str) -> str:
    meta_path = run_root / "meta.json"
    if not meta_path.is_file():
        return fallback
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback
    goal = meta.get("goal")
    if isinstance(goal, str) and goal:
        return goal
    return fallback


def _load_artifacts(run_root: Path) -> dict[str, Artifact]:
    artifacts_path = run_root / "artifacts.json"
    if not artifacts_path.is_file():
        return {}
    try:
        payload = json.loads(artifacts_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}

    out: dict[str, Artifact] = {}
    for role, raw in payload.items():
        if not isinstance(raw, dict):
            continue
        ts = _parse_iso(raw.get("timestamp", ""))
        out[role] = Artifact(
            agent_role=raw.get("agent_role", role),
            type=raw.get("type", "text"),
            content=raw.get("content", ""),
            iteration=int(raw.get("iteration", 0) or 0),
            timestamp=ts or datetime.now(datetime.now().astimezone().tzinfo),
        )
    return out


def _deserialise_signal(payload: dict[str, Any]) -> Signal:
    """Reverse of `worker.job_runner.serialise_signal`. Strict on the
    `type` field — a malformed type means the line was hand-edited or
    written by a different version of the system; fail loudly."""
    type_str = payload["type"]
    sig_type = SignalType(type_str)  # raises if unknown — caller handles
    sig_id = _parse_uuid(payload.get("id"))
    timestamp = _parse_iso(payload.get("timestamp", ""))

    priority: Priority = "normal"
    raw_priority = payload.get("priority")
    if raw_priority in ("normal", "high"):
        priority = raw_priority

    sig = Signal(
        type=sig_type,
        summary=payload.get("summary", ""),
        from_agent=payload.get("from_agent"),
        # `from_user` is auto-set in __post_init__ for user signal types;
        # don't pass it explicitly or the validator complains.
        priority=priority,
        detail=payload.get("detail"),
        iteration=int(payload.get("iteration", 0) or 0),
        seen_by=list(payload.get("seen_by") or []),
    )
    if sig_id is not None:
        sig.id = sig_id
    if timestamp is not None:
        sig.timestamp = timestamp
    return sig


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _parse_uuid(s: Any) -> UUID | None:
    if not s:
        return None
    try:
        return UUID(str(s))
    except (ValueError, TypeError):
        return None
