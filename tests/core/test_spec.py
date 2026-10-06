"""Spec dataclasses, the Spec read view, work.db spec/drafts/messages and the transaction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.backlog import Backlog
from core.spec import (
    Check, Decision, Requirement, Rule, Spec, SpecError, drop_decision, make_checks, next_id,
    sync_decision_rule)
from core.work_db import WorkDB


def _db(tmp_path: Path) -> WorkDB:
    return WorkDB(tmp_path / "projects" / "p")


def _req(rid: str = "R1", **kw: object) -> Requirement:
    base: dict[str, object] = dict(id=rid, text="Users can sign in", kind="feature",
                                   priority="must",
                                   checks=[Check(f"{rid}.1", "Login works"),
                                           Check(f"{rid}.2", "Bad password is refused")])
    base.update(kw)
    return Requirement(**base)  # type: ignore[arg-type]


def test_dataclasses_roundtrip() -> None:
    r = _req(assumed=True)
    assert Requirement.from_dict(json.loads(json.dumps(r.to_dict()))) == r
    d = Decision("D1", "DB", "SQLite", "x" * 25, serves=["R1"], binding=True)
    assert Decision.from_dict(d.to_dict()) == d
    rule = Rule("rule-1", "No globals", "D1")
    assert Rule.from_dict(rule.to_dict()) == rule
    c = Check("R1.1", "t", "met", "run-1")
    assert Check.from_dict(c.to_dict()) == c


def test_empty_spec_doc_defaults(tmp_path: Path) -> None:
    doc = _db(tmp_path).read_spec_doc()
    assert doc == {"version": 0, "next": {"R": 1, "D": 1, "rule": 1},
                   "requirements": [], "decisions": [], "rules": []}


def test_spec_doc_roundtrip_and_rollback(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.spec_doc() as doc:
        doc["version"] = 2
        doc["requirements"].append(_req(next_id(doc, "R")).to_dict())
    again = db.read_spec_doc()
    assert again["version"] == 2 and again["next"]["R"] == 2
    assert again["requirements"][0]["id"] == "R1"
    with pytest.raises(RuntimeError):
        with db.spec_doc() as doc:
            doc["requirements"].append(_req("R9").to_dict())
            raise RuntimeError("boom")
    assert [r["id"] for r in db.read_spec_doc()["requirements"]] == ["R1"]


def test_existing_db_gains_tables_on_open(tmp_path: Path) -> None:
    import sqlite3
    d = tmp_path / "projects" / "old"
    d.mkdir(parents=True)
    conn = sqlite3.connect(d / "work.db")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()
    db = WorkDB(d)
    assert db.read_spec_doc()["version"] == 0
    assert db.read_drafts_doc() == {"next": 1, "drafts": []}
    assert db.list_plan_messages() == []


def test_plan_messages(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.add_plan_message("human", "hello", None, "spec_first")
    db.add_plan_message("pm", "hi", "draft-1", "spec_first")
    db.add_plan_message("human", "third")
    msgs = db.list_plan_messages(2)
    assert [m["text"] for m in msgs] == ["hi", "third"]
    assert msgs[0]["draft_id"] == "draft-1" and msgs[0]["mode"] == "spec_first"
    assert len(db.list_plan_messages()) == 3


def test_transaction_saves_all_three_and_rolls_back(tmp_path: Path) -> None:
    db = _db(tmp_path)
    with db.transaction() as t:
        t.spec["version"] = 1
        t.spec["requirements"].append(_req("R1").to_dict())
        t.drafts["drafts"].append({"id": "draft-1", "base_version": 0, "status": "open"})
        t.drafts["next"] = 2
        t.backlog["items"].append({"id": "B1", "title": "x", "status": "open", "priority": 0})
        t.backlog["next"] = 2
    assert db.read_spec_doc()["version"] == 1
    assert db.read_drafts_doc()["next"] == 2
    assert db.read_backlog_doc()["next"] == 2
    with pytest.raises(RuntimeError):
        with db.transaction() as t:
            t.spec["version"] = 9
            t.backlog["items"].append({"id": "B2", "title": "y", "status": "open", "priority": 0})
            raise RuntimeError("boom")
    assert db.read_spec_doc()["version"] == 1
    assert [i["id"] for i in db.read_backlog_doc()["items"]] == ["B1"]


def test_backlog_doc_still_works_after_spec_changes(tmp_path: Path) -> None:
    b = Backlog(tmp_path, "p")
    it = b.add("Do a thing", ["it works"])
    assert b.get(it.id).title == "Do a thing"


def test_spec_lookup_and_render() -> None:
    doc = {"version": 3, "requirements": [_req("R1").to_dict(),
                                          _req("R2", status="dropped").to_dict()],
           "decisions": [Decision("D1", "DB", "SQLite", "single file, no server needed",
                                  binding=True).to_dict(),
                         Decision("D2", "Old", "x", "y" * 22, status="superseded").to_dict()],
           "rules": [Rule("rule-1", "DB: SQLite", "D1").to_dict(),
                     Rule("rule-2", "Be kind", "human", ["reviewer"], "preference").to_dict(),
                     Rule("rule-3", "Gone", "human", status="dropped").to_dict()]}
    spec = Spec(doc)
    assert spec.version == 3
    assert spec.requirement("R1").text == "Users can sign in"
    assert spec.check("R1.2").text == "Bad password is refused"
    assert spec.get("D1").id == "D1" and spec.get("rule-2").id == "rule-2"
    for bad in ("R9", "D9", "rule-9"):
        with pytest.raises(SpecError):
            spec.get(bad)
    with pytest.raises(SpecError):
        spec.check("R1.9")
    with pytest.raises(SpecError):
        spec.check("nope")
    text = spec.render(10)
    assert "**R1**" in text and "R1.1 (unknown): Login works" in text
    assert "R2" not in text and "D2" not in text and "rule-3" not in text
    assert "(binding)" in text and "because single file" in text
    assert "rule-2" in text
    assert [r.id for r in spec.binding_rules()] == ["rule-1"]
    assert [r.id for r in spec.rules_for("reviewer")] == ["rule-1", "rule-2"]
    assert [r.id for r in spec.rules_for("coder")] == ["rule-1"]


def test_render_limit_and_empty() -> None:
    assert Spec({}).render(5) == ""
    doc = {"requirements": [_req(f"R{i}").to_dict() for i in range(1, 5)]}
    out = Spec(doc).render(2)
    assert "R2" in out and "R3" not in out and "(+2 more)" in out


def test_make_checks_keeps_ids_and_never_reuses() -> None:
    old = [Check("R1.1", "a", "met"), Check("R1.2", "b")]
    new = make_checks("R1", ["B", "c", "  "], old, reserved=5)
    assert [(c.id, c.text, c.state) for c in new] == [("R1.2", "b", "unknown"),
                                                      ("R1.6", "c", "unknown")]


def test_binding_decision_rule_sync() -> None:
    doc: dict = {"next": {"rule": 1}, "rules": []}
    d = Decision("D1", "DB", "SQLite", "r" * 25, binding=True)
    rule = sync_decision_rule(doc, d)
    assert rule is not None and rule.id == "rule-1" and rule.source == "D1"
    assert rule.text == "DB: SQLite"
    d.choice = "Postgres"                     # refresh keeps the same rule
    again = sync_decision_rule(doc, d)
    assert again is not None and again.id == "rule-1" and again.text == "DB: Postgres"
    assert len(doc["rules"]) == 1
    d.status = "superseded"
    assert sync_decision_rule(doc, d) is None
    assert doc["rules"][0]["status"] == "dropped"
    d.status = "accepted"                     # comes back to life, same id
    assert sync_decision_rule(doc, d) is not None
    assert doc["rules"][0]["status"] == "active" and len(doc["rules"]) == 1
    nb = Decision("D2", "X", "Y", "r" * 25, binding=False)
    assert sync_decision_rule(doc, nb) is None and len(doc["rules"]) == 1


def test_drop_decision_drops_its_rule() -> None:
    doc: dict = {"next": {"rule": 1}, "rules": [],
                 "decisions": [Decision("D1", "DB", "SQLite", "r" * 25, binding=True).to_dict()]}
    sync_decision_rule(doc, Decision.from_dict(doc["decisions"][0]))
    dropped = drop_decision(doc, "D1", at=1.0, by="human", reason="changed our mind")
    assert dropped.status == "dropped" and dropped.version == 2
    assert doc["decisions"][0]["history"][0]["dropped"] == "changed our mind"
    assert doc["rules"][0]["status"] == "dropped"
    with pytest.raises(SpecError):
        drop_decision(doc, "D1", at=2.0, by="human")
