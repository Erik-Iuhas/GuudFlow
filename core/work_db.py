"""SQLite storage for a project's ongoing work (plan §14.8).

One database per project, `data/projects/{id}/work.db` (WAL mode), holds:

  backlog_items  one row per backlog item (JSON in `data`, status/priority indexed)
  run_states     one row per run submitted to the project repo (JSON in `data`)
  meta           counters (the next backlog id)

`backlog_doc()` / `runs_doc()` give callers the same document shape the JSON files
had ({"next": n, "items": [...]} and {run_id: row}) inside a `BEGIN IMMEDIATE`
transaction, so the API and the worker never interleave writes; reads need no lock.
Existing `backlog.json` / `runs_state.json` files are imported once, on first open,
and renamed to `*.migrated`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.json_store import file_lock

BUSY_TIMEOUT_S = 15.0
_SCHEMA = """
CREATE TABLE IF NOT EXISTS backlog_items (
    id TEXT PRIMARY KEY, num INTEGER NOT NULL, status TEXT NOT NULL,
    priority INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_states (
    run_id TEXT PRIMARY KEY, submitted_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _num(item_id: str) -> int:
    return int(item_id[1:]) if item_id[1:].isdigit() else 0


class WorkDB:
    """The work database of one project."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / "work.db"
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if not self._ready:
            # Switching to WAL does not wait on the busy timeout, so the one-time
            # setup (WAL, schema, JSON import) runs under a file lock.
            with file_lock(self.path):
                conn = self._open()
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SCHEMA)
                    self._migrate(conn)
                finally:
                    conn.close()
            self._ready = True
        return self._open()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    # ── backlog ─────────────────────────────────────────────────────────────
    def read_backlog_doc(self) -> dict[str, Any]:  # Any: JSON document
        """The backlog as {"next": n, "items": [...]} (no lock)."""
        conn = self._connect()
        try:
            return self._backlog(conn)
        finally:
            conn.close()

    @contextmanager
    def backlog_doc(self) -> Iterator[dict[str, Any]]:  # Any: JSON document
        """Read-modify-write the backlog in one transaction (saved if the block succeeds)."""
        with self._write() as conn:
            doc = self._backlog(conn)
            yield doc
            for row in doc.get("items", []):
                conn.execute(
                    "INSERT INTO backlog_items(id, num, status, priority, data) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "status=excluded.status, priority=excluded.priority, data=excluded.data",
                    (row["id"], _num(str(row["id"])), str(row.get("status", "open")),
                     int(row.get("priority", 0)), json.dumps(row)))
            conn.execute("INSERT INTO meta(key, value) VALUES ('next_item', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (str(int(doc.get("next", 1))),))

    @staticmethod
    def _backlog(conn: sqlite3.Connection) -> dict[str, Any]:  # Any: JSON document
        rows = conn.execute("SELECT data FROM backlog_items ORDER BY num").fetchall()
        nxt = conn.execute("SELECT value FROM meta WHERE key='next_item'").fetchone()
        return {"next": int(nxt[0]) if nxt else 1, "items": [json.loads(r[0]) for r in rows]}

    # ── run states ──────────────────────────────────────────────────────────
    def read_runs_doc(self) -> dict[str, dict[str, Any]]:  # Any: JSON rows
        """Run states as {run_id: row} (no lock)."""
        conn = self._connect()
        try:
            return self._runs(conn)
        finally:
            conn.close()

    @contextmanager
    def runs_doc(self) -> Iterator[dict[str, dict[str, Any]]]:  # Any: JSON rows
        """Read-modify-write run states in one transaction."""
        with self._write() as conn:
            doc = self._runs(conn)
            yield doc
            for run_id, row in doc.items():
                conn.execute(
                    "INSERT INTO run_states(run_id, submitted_at, data) VALUES (?, ?, ?) "
                    "ON CONFLICT(run_id) DO UPDATE SET submitted_at=excluded.submitted_at, "
                    "data=excluded.data",
                    (run_id, float(row.get("submitted_at") or 0.0), json.dumps(row)))

    @staticmethod
    def _runs(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:  # Any: JSON rows
        rows = conn.execute("SELECT run_id, data FROM run_states").fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    # ── one-time import of the JSON files ───────────────────────────────────
    def _migrate(self, conn: sqlite3.Connection) -> None:
        backlog = self.project_dir / "backlog.json"
        runs = self.project_dir / "runs_state.json"
        if backlog.is_file():
            doc = json.loads(backlog.read_text(encoding="utf-8") or "{}")
            conn.execute("BEGIN IMMEDIATE")
            for row in doc.get("items", []):
                conn.execute("INSERT OR IGNORE INTO backlog_items(id, num, status, priority, data) "
                             "VALUES (?, ?, ?, ?, ?)",
                             (row["id"], _num(str(row["id"])), str(row.get("status", "open")),
                              int(row.get("priority", 0)), json.dumps(row)))
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('next_item', ?)",
                         (str(int(doc.get("next", 1))),))
            conn.execute("COMMIT")
            backlog.rename(backlog.with_name("backlog.json.migrated"))
        if runs.is_file():
            doc = json.loads(runs.read_text(encoding="utf-8") or "{}")
            conn.execute("BEGIN IMMEDIATE")
            for run_id, row in doc.items():
                conn.execute("INSERT OR IGNORE INTO run_states(run_id, submitted_at, data) "
                             "VALUES (?, ?, ?)",
                             (run_id, float(row.get("submitted_at") or 0.0), json.dumps(row)))
            conn.execute("COMMIT")
            runs.rename(runs.with_name("runs_state.json.migrated"))
