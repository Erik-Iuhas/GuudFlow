"""The run's console: what GuudFlow itself was doing, kept with the run.

`console.jsonl` collects the log lines of the host-side code that served the run (scheduler,
spawns, gateway, git, errors with their tracebacks) plus a few notes the runner writes on
purpose (the team at start, why nothing was spawned at the end). The board shows what agents
said and did; this shows why something did not happen. `read_console` also returns the tail of
each role's opencode server log. Studio's run view shows both on its Console tab.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import traceback
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

CONSOLE_FILE = "console.jsonl"
MAX_LINE_CHARS = 4000
_LOGGERS = ("core", "gateway", "worker", "api")

_current: ContextVar[Path | None] = ContextVar("guudflow_run_console", default=None)


def _write(run_root: Path, source: str, level: str, text: str) -> None:
    row = {"t": time.time(), "source": source, "level": level, "text": text[:MAX_LINE_CHARS]}
    try:
        with (run_root / CONSOLE_FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass  # the console must never break a run


class _Handler(logging.Handler):
    """Writes records emitted while a run's console is active to that run's file."""

    def emit(self, record: logging.LogRecord) -> None:
        run_root = _current.get()
        if run_root is None:
            return
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + "".join(traceback.format_exception(*record.exc_info))
        _write(run_root, record.name, record.levelname.lower(), text)


_handler: _Handler | None = None


def _install() -> None:
    global _handler
    if _handler is not None:
        return
    _handler = _Handler(level=logging.INFO)
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        logger.addHandler(_handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)


@contextlib.contextmanager
def capture(run_root: Path) -> Iterator[None]:
    """Collect the run's host-side log lines (tasks started inside inherit it)."""
    _install()
    token = _current.set(Path(run_root))
    try:
        yield
    finally:
        _current.reset(token)


def note(run_root: Path, source: str, text: str, level: str = "info") -> None:
    """A line the runner writes on purpose."""
    _write(Path(run_root), source, level, text)


def _tail(path: Path, lines: int) -> list[str]:
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 64_000))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-lines:]


def read_console(run_root: Path, tail: int = 400, role_tail: int = 40) -> dict[str, Any]:  # Any: JSON
    """The last `tail` console lines and each role's opencode server log tail."""
    rows: list[dict[str, Any]] = []  # Any: JSON rows
    for line in _tail(Path(run_root) / CONSOLE_FILE, tail):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    roles = []
    home = Path(run_root) / "opencode_home"
    if home.is_dir():
        for d in sorted(p for p in home.iterdir() if p.is_dir()):
            log = _tail(d / "opencode-serve.log", role_tail)
            if log:
                roles.append({"role": d.name, "lines": log})
    return {"lines": rows, "roles": roles}


def describe_end(agents: list[Any], status: str, spawn_counts: dict[str, int],  # Any: AgentConfig
                 backlog_items: int, task_mode: bool,
                 last_results: dict[str, str] | None = None, cards: int | None = None) -> str:
    """Why the run ended the way it did, in one block: who ran, who never did and what each
    waited on, so a run that stopped before its first call is explained."""
    lines = [f"run ended: {status}; mode: {'task cards' if task_mode else 'plain roles'}; "
             f"backlog items taken: {backlog_items}"
             + (f"; cards created: {cards}" if cards is not None else "")]
    last_results = last_results or {}
    for a in agents:
        n = spawn_counts.get(a.role, 0)
        deps = ", ".join(a.depends_on or []) or "nothing"
        line = f"- {a.role}: spawned {n}x" + ("" if n else f", never started (waits on {deps})")
        if a.role in last_results:
            line += f"; last result: {last_results[a.role]}"
        lines.append(line)
    if task_mode and cards == 0 and any(spawn_counts.values()):
        lines.append("the planner ran but created no cards, so no builder could start: its "
                     "last result above says why (see also its server log in the source list)")
    if not any(spawn_counts.get(a.role, 0) for a in agents):
        lines.append("no role was ever spawned: look above for spawn errors, a missing model key, "
                     "or a team with nothing runnable")
    return "\n".join(lines)
