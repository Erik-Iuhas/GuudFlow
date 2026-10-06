"""SQLite storage for a project's ongoing work (plan §14.8).

One database per project, `data/projects/{id}/work.db` (WAL mode), holds:

  backlog_items  one row per backlog item (JSON in `data`, status/priority indexed)
  run_states     one row per run submitted to the project repo (JSON in `data`)
  meta           counters (the next backlog id, spec version and counters, next draft)
  spec_items     requirements, decisions and rules of the project spec (JSON in `data`)
  plan_messages  the plan conversation (append-only)
  drafts         spec/backlog change drafts (JSON in `data`)

`backlog_doc()` / `runs_doc()` give callers the same document shape the JSON files
had ({"next": n, "items": [...]} and {run_id: row}) inside a `BEGIN IMMEDIATE`
transaction, so the API and the worker never interleave writes; reads need no lock.
Existing `backlog.json` / `runs_state.json` files are imported once, on first open,
and renamed to `*.migrated`.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
CREATE TABLE IF NOT EXISTS spec_items (
    kind TEXT NOT NULL, id TEXT PRIMARY KEY, num INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plan_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, author TEXT NOT NULL,
    text TEXT NOT NULL, draft_id TEXT, mode TEXT NOT NULL DEFAULT '',
    cost_usd REAL, tokens INTEGER);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, base_version INTEGER NOT NULL, status TEXT NOT NULL,
    data TEXT NOT NULL);
"""
_SPEC_KINDS = (("requirements", "requirement"), ("decisions", "decision"), ("rules", "rule"))
_COUNTERS = ("R", "D", "rule")


def _num(item_id: str) -> int:
    return int(item_id[1:]) if item_id[1:].isdigit() else 0


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _spec_num(item_id: str) -> int:
    found = re.search(r"([0-9]+)$", item_id)
    return int(found.group(1)) if found else 0


@dataclass
class WorkTxn:
    """The documents one transaction may change together (see `WorkDB.transaction`)."""

    backlog: dict[str, Any]  # Any: JSON document
    spec: dict[str, Any]     # Any: JSON document
    drafts: dict[str, Any]   # Any: JSON document


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
            self._save_backlog(conn, doc)

    @staticmethod
    def _save_backlog(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:  # Any: JSON
        for row in doc.get("items", []):
            conn.execute(
                "INSERT INTO backlog_items(id, num, status, priority, data) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, priority=excluded.priority, data=excluded.data",
                (row["id"], _num(str(row["id"])), str(row.get("status", "open")),
                 int(row.get("priority", 0)), json.dumps(row)))
        _set_meta(conn, "next_item", str(int(doc.get("next", 1))))

    @staticmethod
    def _backlog(conn: sqlite3.Connection) -> dict[str, Any]:  # Any: JSON document
        rows = conn.execute("SELECT data FROM backlog_items ORDER BY num").fetchall()
        nxt = conn.execute("SELECT value FROM meta WHERE key='next_item'").fetchone()
        return {"next": int(nxt[0]) if nxt else 1, "items": [json.loads(r[0]) for r in rows]}

    # ── spec ────────────────────────────────────────────────────────────────
    def read_spec_doc(self) -> dict[str, Any]:  # Any: JSON document
        """The spec as {"version", "next": {"R","D","rule"}, "requirements", "decisions",
        "rules"} (no lock)."""
        conn = self._connect()
        try:
            return self._spec(conn)
        finally:
            conn.close()

    @contextmanager
    def spec_doc(self) -> Iterator[dict[str, Any]]:  # Any: JSON document
        """Read-modify-write the spec in one transaction (saved if the block succeeds)."""
        with self._write() as conn:
            doc = self._spec(conn)
            yield doc
            self._save_spec(conn, doc)

    @staticmethod
    def _spec(conn: sqlite3.Connection) -> dict[str, Any]:  # Any: JSON document
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        doc: dict[str, Any] = {
            "version": int(meta.get("spec_version", 0)),
            "next": {c: int(meta.get(f"next_{c}", 1)) for c in _COUNTERS}}
        for key, kind in _SPEC_KINDS:
            rows = conn.execute("SELECT data FROM spec_items WHERE kind=? ORDER BY num",
                                (kind,)).fetchall()
            doc[key] = [json.loads(r[0]) for r in rows]
        return doc

    @staticmethod
    def _save_spec(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:  # Any: JSON
        for key, kind in _SPEC_KINDS:
            for row in doc.get(key, []):
                conn.execute(
                    "INSERT INTO spec_items(kind, id, num, data) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                    (kind, row["id"], _spec_num(str(row["id"])), json.dumps(row)))
        _set_meta(conn, "spec_version", str(int(doc.get("version", 0))))
        for c in _COUNTERS:
            _set_meta(conn, f"next_{c}", str(int(doc.get("next", {}).get(c, 1))))

    # ── drafts ──────────────────────────────────────────────────────────────
    def read_drafts_doc(self) -> dict[str, Any]:  # Any: JSON document
        """Drafts as {"next": n, "drafts": [...]} (no lock)."""
        conn = self._connect()
        try:
            return self._drafts(conn)
        finally:
            conn.close()

    @contextmanager
    def drafts_doc(self) -> Iterator[dict[str, Any]]:  # Any: JSON document
        """Read-modify-write the drafts in one transaction."""
        with self._write() as conn:
            doc = self._drafts(conn)
            yield doc
            self._save_drafts(conn, doc)

    @staticmethod
    def _drafts(conn: sqlite3.Connection) -> dict[str, Any]:  # Any: JSON document
        rows = conn.execute("SELECT data FROM drafts").fetchall()
        drafts = sorted((json.loads(r[0]) for r in rows),
                        key=lambda d: _spec_num(str(d["id"])))
        nxt = conn.execute("SELECT value FROM meta WHERE key='next_draft'").fetchone()
        return {"next": int(nxt[0]) if nxt else 1, "drafts": drafts}

    @staticmethod
    def _save_drafts(conn: sqlite3.Connection, doc: dict[str, Any]) -> None:  # Any: JSON
        for row in doc.get("drafts", []):
            conn.execute(
                "INSERT INTO drafts(id, base_version, status, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET base_version=excluded.base_version, "
                "status=excluded.status, data=excluded.data",
                (row["id"], int(row.get("base_version", 0)), str(row.get("status", "open")),
                 json.dumps(row)))
        _set_meta(conn, "next_draft", str(int(doc.get("next", 1))))

    @contextmanager
    def transaction(self) -> Iterator[WorkTxn]:
        """Backlog, spec and drafts in ONE transaction; all saved if the block succeeds,
        none if it raises (draft approval)."""
        with self._write() as conn:
            txn = WorkTxn(self._backlog(conn), self._spec(conn), self._drafts(conn))
            yield txn
            self._save_backlog(conn, txn.backlog)
            self._save_spec(conn, txn.spec)
            self._save_drafts(conn, txn.drafts)

    # ── plan messages ───────────────────────────────────────────────────────
    def add_plan_message(self, author: str, text: str, draft_id: str | None = None,
                         mode: str = "", cost_usd: float | None = None,
                         tokens: int | None = None) -> dict[str, Any]:  # Any: JSON row
        """Append one message to the plan conversation (a PM message may carry its usage)."""
        now = time.time()
        with self._write() as conn:
            cur = conn.execute("INSERT INTO plan_messages(at, author, text, draft_id, mode, "
                               "cost_usd, tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (now, author, text, draft_id, mode, cost_usd, tokens))
            return {"id": cur.lastrowid, "at": now, "author": author, "text": text,
                    "draft_id": draft_id, "mode": mode, "cost_usd": cost_usd, "tokens": tokens}

    def list_plan_messages(self, limit: int = 100) -> list[dict[str, Any]]:  # Any: JSON rows
        """The latest `limit` plan messages, oldest first (no lock)."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT id, at, author, text, draft_id, mode, "
                                "cost_usd, tokens FROM plan_messages "
                                "ORDER BY id DESC LIMIT ?", (max(0, limit),)).fetchall()
        finally:
            conn.close()
        keys = ("id", "at", "author", "text", "draft_id", "mode", "cost_usd", "tokens")
        return [dict(zip(keys, r)) for r in reversed(rows)]

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
        columns = {r[1] for r in conn.execute("PRAGMA table_info(plan_messages)")}
        for name, kind in (("cost_usd", "REAL"), ("tokens", "INTEGER")):
            if name not in columns:  # databases made before the PM reported usage
                conn.execute(f"ALTER TABLE plan_messages ADD COLUMN {name} {kind}")
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
