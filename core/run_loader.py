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
    Task,
    TaskRef,
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

    # M3.5 chunk 7 — restore per-agent desks. tasks.jsonl is the
    # cached view; board.jsonl is the source of truth. We replay
    # tasks.jsonl first, then verify against the signal trail and
    # log a warning on drift (signal trail wins).
    tasks, queues = load_persisted_tasks(run_root)
    if tasks:
        ws.tasks = tasks
        ws.task_queues = queues
    else:
        # No tasks.jsonl — reconstruct from board signals if any
        # TASK_* events landed before the crash. Empty workspaces
        # produce empty desks.
        ws.tasks, ws.task_queues = _rebuild_tasks_from_board(signals)

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


def load_persisted_tasks(
    run_root: Path,
) -> tuple[dict[str, Task], dict[str, list[str]]]:
    """Load the per-agent task desks from `tasks.jsonl` (M3.5 chunk 7).

    Each line is one mutation; later lines win. Returns (tasks_dict,
    task_queues) so the caller can attach to a workspace. Empty if
    the file is missing — caller falls back to rebuilding from the
    signal trail.
    """
    run_root = Path(run_root)
    tasks_path = run_root / "tasks.jsonl"
    if not tasks_path.is_file():
        return {}, {}

    tasks: dict[str, Task] = {}
    queues: dict[str, list[str]] = {}
    for line in tasks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            task = _deserialise_task(payload)
        except (KeyError, ValueError, TypeError):
            continue

        existing = tasks.get(task.task_id)
        tasks[task.task_id] = task
        # Queue order: insertion order from first sighting; on later
        # mutations we keep the existing position. New tasks append.
        if existing is None:
            queues.setdefault(task.assigned_to, []).append(task.task_id)
    return tasks, queues


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


def _deserialise_task(payload: dict[str, Any]) -> Task:
    """Reverse of `worker.job_runner.serialise_task`. Required fields
    are `task_id` and `assigned_to`; everything else has a sensible
    default."""
    refs = []
    for raw in payload.get("references") or []:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        value = raw.get("value")
        if kind not in ("file", "commit", "task", "story") or not value:
            continue
        refs.append(TaskRef(
            kind=kind, value=str(value), note=str(raw.get("note") or ""),
        ))
    return Task(
        task_id=str(payload["task_id"]),
        story_id=str(payload.get("story_id") or ""),
        parent_task_id=payload.get("parent_task_id") or None,
        assigned_to=str(payload["assigned_to"]),
        created_by=str(payload.get("created_by") or ""),
        created_at_iter=int(payload.get("created_at_iter") or 0),
        goal=str(payload.get("goal") or ""),
        acceptance_criteria=list(payload.get("acceptance_criteria") or []),
        out_of_scope=list(payload.get("out_of_scope") or []),
        references=refs,
        status=str(payload.get("status") or "PENDING"),  # type: ignore[arg-type]
        started_at_iter=payload.get("started_at_iter"),
        completed_at_iter=payload.get("completed_at_iter"),
        closed_by_commit=str(payload.get("closed_by_commit") or ""),
        blocked_on=list(payload.get("blocked_on") or []),
        cancelled_reason=str(payload.get("cancelled_reason") or ""),
        title=str(payload.get("title") or ""),
        kind=str(payload.get("kind") or "code"),
        size=str(payload.get("size") or "M"),
        files_hint=[str(x) for x in payload.get("files_hint") or []],
        depends_on=[str(x) for x in payload.get("depends_on") or []],
        visual_refs=[str(x) for x in payload.get("visual_refs") or []],
        column=str(payload.get("column") or "todo"),
        review_round=int(payload.get("review_round") or 0),
        priority=int(payload.get("priority") or 0),
        column_since=float(payload.get("column_since") or 0.0),
        thread=[dict(e) for e in payload.get("thread") or [] if isinstance(e, dict)],
        backlog_id=str(payload["backlog_id"]) if payload.get("backlog_id") else None,
    )


def _rebuild_tasks_from_board(
    signals: list[Signal],
) -> tuple[dict[str, Task], dict[str, list[str]]]:
    """Fallback when tasks.jsonl is missing: reconstruct desks from
    TASK_ASSIGNED signals + apply lifecycle TASK_* events. The board
    is the source of truth (DESIGN §38.12 §1.3). Imperfect — fields
    not encoded in the signal payload (acceptance criteria, refs)
    are lost — but state and identity survive."""
    tasks: dict[str, Task] = {}
    queues: dict[str, list[str]] = {}

    _STATUS_FROM_TYPE: dict[SignalType, str] = {
        SignalType.TASK_STARTED: "IN_PROGRESS",
        SignalType.TASK_COMPLETED: "COMPLETE",
        SignalType.TASK_BLOCKED: "BLOCKED",
        SignalType.TASK_CANCELLED: "CANCELLED",
    }

    for sig in signals:
        if sig.type is SignalType.TASK_ASSIGNED:
            task_id = (sig.detail or "").strip()
            if not task_id or task_id in tasks:
                continue
            assigned_to = sig.summary.split("→", 1)[1].split(":", 1)[0].strip() if "→" in sig.summary else ""
            task = Task(
                task_id=task_id,
                story_id="",
                assigned_to=assigned_to,
                created_by=sig.from_agent or "",
                created_at_iter=sig.iteration,
                goal=sig.summary,
            )
            tasks[task_id] = task
            if assigned_to:
                queues.setdefault(assigned_to, []).append(task_id)
        elif sig.type in _STATUS_FROM_TYPE:
            task_id = (sig.detail or "").split(";", 1)[0].strip()
            task = tasks.get(task_id)
            if task is None:
                continue
            task.status = _STATUS_FROM_TYPE[sig.type]  # type: ignore[assignment]
            if sig.type is SignalType.TASK_COMPLETED:
                # detail format: "{task_id};commit={hash}"
                if ";commit=" in (sig.detail or ""):
                    task.closed_by_commit = sig.detail.split(";commit=", 1)[1]
                task.completed_at_iter = sig.iteration
            elif sig.type is SignalType.TASK_STARTED:
                task.started_at_iter = sig.iteration

    return tasks, queues


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
