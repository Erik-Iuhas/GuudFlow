"""Tests for core.work_db (plan §14.8): the project's work in SQLite."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from core.backlog import Backlog
from core.work_db import WorkDB


def test_json_files_are_imported_once(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "p"
    project.mkdir(parents=True)
    (project / "backlog.json").write_text(json.dumps({"next": 3, "items": [
        {"id": "B1", "title": "Old item", "status": "open", "priority": 2},
        {"id": "B2", "title": "Done item", "status": "done", "priority": 0}]}))
    (project / "runs_state.json").write_text(json.dumps(
        {"run_a": {"run_id": "run_a", "state": "pending", "base": "x", "submitted_at": 1.0}}))
    backlog = Backlog(tmp_path, "p")
    assert [i.id for i in backlog.items()] == ["B1", "B2"]
    assert backlog.add("New", []).id == "B3"                 # the counter came along
    assert WorkDB(project).read_runs_doc()["run_a"]["state"] == "pending"
    assert (project / "backlog.json.migrated").is_file() and not (project / "backlog.json").exists()


def test_a_failed_change_is_rolled_back(tmp_path: Path) -> None:
    db = WorkDB(tmp_path)
    with db.backlog_doc() as doc:
        doc["items"].append({"id": "B1", "title": "kept", "status": "open", "priority": 0})
        doc["next"] = 2
    with pytest.raises(RuntimeError):
        with db.backlog_doc() as doc:
            doc["items"][0]["title"] = "changed"
            raise RuntimeError("boom")
    assert db.read_backlog_doc()["items"][0]["title"] == "kept"


def test_concurrent_writers_never_lose_an_item(tmp_path: Path) -> None:
    backlog = Backlog(tmp_path, "p")
    errors: list[BaseException] = []

    def add(n: int) -> None:
        try:
            for i in range(10):
                Backlog(tmp_path, "p").add(f"item {n}-{i}", [])
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    ids = [i.id for i in backlog.items()]
    assert len(ids) == 40 and len(set(ids)) == 40
