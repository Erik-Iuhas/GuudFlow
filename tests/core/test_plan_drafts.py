"""Plan turns, validation, draft lifecycle and atomic approval. No LLM calls."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from core.backlog import Backlog, BacklogError, BacklogItem
from core.plan_drafts import (
    AddDecision, AddItem, AddRequirement, AddRule, DropRequirement, DropRule, EditItem,
    EditRequirement, PlanDraftError, PlanDrafts, PlanTurn, SupersedeDecision, validate_turn)
from core.spec import Spec, resolve_acceptance
from tests.spec_fixtures import with_reasons

REASON = "because the team already runs it"


def _turn(*changes: dict[str, Any], reply: str = "ok") -> PlanTurn:
    return PlanTurn.model_validate(with_reasons({"reply": reply, "changes": list(changes)}))


def req(ref: str = "new:1", **kw: Any) -> dict[str, Any]:
    return {"op": "add_requirement", "ref": ref, "text": "Users can sign in", "kind": "feature",
            "priority": "must", "checks": ["login works", "bad password refused"], **kw}


def dec(ref: str = "new:2", **kw: Any) -> dict[str, Any]:
    return {"op": "add_decision", "ref": ref, "title": "Database", "choice": "SQLite",
            "reason": REASON, **kw}


def item(ref: str = "new:3", **kw: Any) -> dict[str, Any]:
    return {"op": "add_item", "ref": ref, "title": "Build login", **kw}


@pytest.fixture
def pd(tmp_path: Path) -> PlanDrafts:
    return PlanDrafts(tmp_path, "p")


def _problems(pd: PlanDrafts, turn: PlanTurn) -> list[str]:
    return validate_turn(turn, Spec(pd.db.read_spec_doc()), pd.db.read_backlog_doc())


def _approve_all(pd: PlanDrafts, turn: PlanTurn, lead_held: bool = False) -> Any:
    d = pd.open_draft()
    d = pd.merge_pm_turn(d, turn, lead_held)
    for c in d.changes:
        if c.accepted is None:
            pd.set_change(d.id, c.cid, True)
    return pd.approve(d.id, "erik")


# ── model ───────────────────────────────────────────────────────────────────
def test_turn_limits_and_unknown_op() -> None:
    with pytest.raises(ValidationError):
        _turn(*[req(f"new:{i}") for i in range(1, 22)])
    with pytest.raises(ValidationError):
        _turn({"op": "explode", "id": "R1"})
    with pytest.raises(ValidationError):
        _turn({"op": "drop_requirement", "id": "bogus", "reason": "nope"})
    assert _turn(req(), reply="x").questions == []


def test_every_op_parses() -> None:
    t = _turn(req(), dec(), item(), {"op": "edit_requirement", "id": "R1", "text": "new"},
              {"op": "drop_requirement", "id": "R1", "reason": "gone"},
              {"op": "supersede_decision", "id": "D1", "ref": "new:4", "title": "Title",
               "choice": "c", "reason": REASON},
              {"op": "edit_item", "id": "B1", "priority": 2},
              {"op": "add_rule", "text": "No globals"},
              {"op": "drop_rule", "id": "rule-1", "reason": "obsolete"})
    kinds = [type(c) for c in t.changes]
    assert kinds == [AddRequirement, AddDecision, AddItem, EditRequirement, DropRequirement,
                     SupersedeDecision, EditItem, AddRule, DropRule]


# ── validation ──────────────────────────────────────────────────────────────
def test_valid_turn_has_no_problems(pd: PlanDrafts) -> None:
    t = _turn(req(), dec(serves=["new:1"]), item(serves=["new:1"]))
    assert _problems(pd, t) == []


def test_requirement_needs_check_unless_non_goal(pd: PlanDrafts) -> None:
    assert any("at least one check" in p
               for p in _problems(pd, _turn(req(checks=[]))))
    assert _problems(pd, _turn(req(checks=[], kind="non_goal"))) == []


def test_decision_reason_too_short(pd: PlanDrafts) -> None:
    assert any("20" in p for p in _problems(pd, _turn(dec(reason="too short"))))


def test_unknown_ids_and_refs(pd: PlanDrafts) -> None:
    t = _turn({"op": "edit_requirement", "id": "R7", "text": "x"},
              {"op": "drop_requirement", "id": "R8", "reason": "gone"},
              {"op": "edit_item", "id": "B5", "priority": 1},
              {"op": "drop_rule", "id": "rule-4", "reason": "gone"},
              {"op": "supersede_decision", "id": "D3", "ref": "new:1", "title": "Title",
               "choice": "c", "reason": REASON},
              item("new:2", serves=["new:9", "R5", "junk"], depends_on=["new:8", "B4"]))
    out = "\n".join(_problems(pd, t))
    for needle in ("unknown requirement R7", "unknown requirement R8", "unknown item B5",
                   "unknown rule rule-4", "unknown decision D3", "new:9", "unknown requirement R5",
                   "'junk'", "new:8", "unknown item B4"):
        assert needle in out, needle


def test_refs_resolve_within_the_turn_and_are_unique(pd: PlanDrafts) -> None:
    assert _problems(pd, _turn(req("new:1"), item("new:2", serves=["new:1"],
                                                  depends_on=["new:3"]), item("new:3"))) == []
    out = _problems(pd, _turn(req("new:1"), req("new:1")))
    assert any("used twice" in p for p in out)
    # a ref of the wrong kind
    out = _problems(pd, _turn(item("new:1"), item("new:2", serves=["new:1"])))
    assert any("not a requirement" in p for p in out)


def test_dependency_cycles(pd: PlanDrafts) -> None:
    bl = Backlog(pd.db.project_dir.parent.parent, "p")
    b1 = bl.add("First one")
    b2 = bl.add("Second one", depends_on=[b1.id])
    out = _problems(pd, _turn({"op": "edit_item", "id": b1.id, "depends_on": [b2.id]}))
    assert any("cycle" in p for p in out)
    out = _problems(pd, _turn(item("new:1", depends_on=["new:2"]),
                              item("new:2", depends_on=["new:1"])))
    assert any("cycle" in p for p in out)
    assert _problems(pd, _turn(item("new:1", depends_on=[b2.id]))) == []


def test_dropped_requirement_cannot_be_served_or_edited(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req()))
    _approve_all(pd, _turn({"op": "drop_requirement", "id": "R1", "reason": "descoped"}))
    out = _problems(pd, _turn(item(serves=["R1"]),
                              {"op": "edit_requirement", "id": "R1", "text": "z"}))
    assert sum("dropped" in p for p in out) == 2


def test_pinned_fields_refused(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req(), item(serves=["new:1"])))
    pd.apply_direct(EditRequirement(op="edit_requirement", id="R1", priority="should"), "erik")
    pd.apply_direct(EditItem(op="edit_item", id="B1", title="Human title"), "erik")
    out = _problems(pd, _turn({"op": "edit_requirement", "id": "R1", "priority": "could"},
                              {"op": "edit_item", "id": "B1", "title": "PM title"}))
    assert len(out) == 2 and all("human-set" in p for p in out)
    # other fields are fine
    assert _problems(pd, _turn({"op": "set_statement", "id": "R1", "text": "reworded"},
                               {"op": "edit_item", "id": "B1", "notes": "x"})) == []


def test_empty_edit_and_done_item(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req()))
    out = _problems(pd, _turn({"op": "edit_requirement", "id": "R1"}))
    assert any("no field" in p for p in out)


# ── approval ────────────────────────────────────────────────────────────────
def test_approve_applies_everything(pd: PlanDrafts) -> None:
    turn = _turn(req(), dec(serves=["new:1"], binding=True), item(serves=["new:1"]),
                 {"op": "add_rule", "text": "Keep functions short", "applies_to": ["coder"],
                  "strength": "preference", "ref": "new:4"})
    res = _approve_all(pd, turn)
    assert res.version == 1
    assert res.created == {"new:1": "R1", "new:2": "D1", "new:3": "B1", "new:4": "rule-1"}
    assert res.applied == ["c1", "c2", "c3", "c4"]
    spec = Spec(pd.db.read_spec_doc())
    r = spec.requirement("R1")
    assert [c.id for c in r.checks] == ["R1.1", "R1.2"] and r.source == "pm"
    d = spec.decision("D1")
    assert d.serves == ["R1"] and d.binding and d.decided_by == "pm"
    assert [x.id for x in spec.binding_rules()] == ["rule-2"]   # D1's rule; rule-1 is the add_rule
    assert spec.rule("rule-2").source == "D1"
    assert spec.rule("rule-1").strength == "preference" and spec.rule("rule-1").source == "pm"
    assert spec.version == 1
    d_row = pd.get("draft-1")
    assert d_row.status == "approved" and d_row.approved_by == "erik" and d_row.version == 1


def test_item_acceptance_copies_checks_with_ids_then_own_lines(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req(), item(serves=["new:1"],
                                       acceptance=["R1.1: login works", "docs updated", ""])))
    it = Backlog(pd.db.project_dir.parent.parent, "p").get("B1")
    assert it.serves == ["R1"]
    # Items reference checks by id (plan §11.2); readers resolve them to the current text.
    assert it.acceptance == ["R1.1", "R1.2", "docs updated"]
    assert resolve_acceptance(Spec(pd.db.read_spec_doc()), it.acceptance) == [
        "R1.1: login works", "R1.2: bad password refused", "docs updated"]
    assert it.source == "pm"


def test_item_serves_existing_requirement_and_dependencies(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req()))
    res = _approve_all(pd, _turn(item("new:1", depends_on=["new:2"], serves=["R1"]),
                                 item("new:2", title="Schema first")))
    assert res.created == {"new:1": "B1", "new:2": "B2"}
    bl = Backlog(pd.db.project_dir.parent.parent, "p")
    assert bl.get("B1").depends_on == ["B2"] and bl.get("B1").acceptance[0] == "R1.1"


def test_edit_requirement_history_checks_and_version(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req()))
    _approve_all(pd, _turn({"op": "edit_requirement", "id": "R1", "text": "Users can log in",
                            "checks": ["bad password refused", "session expires"]}))
    r = Spec(pd.db.read_spec_doc()).requirement("R1")
    assert r.version == 2 and r.text == "Users can log in"
    assert [(c.id, c.text) for c in r.checks] == [("R1.2", "bad password refused"),
                                                  ("R1.3", "session expires")]
    h = r.history[0]
    assert h["version"] == 1 and h["text"] == "Users can sign in" and h["by"] == "erik"
    assert h["draft"] == "draft-2" and len(h["checks"]) == 2 and h["priority"] == "must"
    assert Spec(pd.db.read_spec_doc()).version == 2


def test_drop_requirement_and_rule(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req(), {"op": "add_rule", "text": "Be tidy"}))
    _approve_all(pd, _turn({"op": "drop_requirement", "id": "R1", "reason": "descoped"},
                           {"op": "drop_rule", "id": "rule-1", "reason": "obsolete"}))
    spec = Spec(pd.db.read_spec_doc())
    assert spec.requirement("R1").status == "dropped"
    assert spec.requirement("R1").history[0]["reason"] == "descoped"
    assert spec.rule("rule-1").status == "dropped"


def test_drop_item(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(item("new:1"), item("new:2", title="Old idea")))
    _approve_all(pd, _turn({"op": "drop_item", "id": "B2", "reason": "merged into B1"}))
    rows = {r["id"]: r for r in pd.db.read_backlog_doc()["items"]}
    assert rows["B2"]["status"] == "dropped" and rows["B1"]["status"] == "open"
    assert "merged into B1" in rows["B2"]["history"][-1]["decision"]
    assert any("B2 is dropped" in p for p in
               _problems(pd, _turn({"op": "drop_item", "id": "B2", "reason": "again"})))
    assert any("unknown item B9" in p for p in
               _problems(pd, _turn({"op": "drop_item", "id": "B9", "reason": "gone"})))


def test_drop_item_refused_while_open_items_depend_on_it(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(item("new:1"), item("new:2", title="Next", depends_on=["new:1"])))
    drop_b1 = {"op": "drop_item", "id": "B1", "reason": "not needed"}
    assert any("B2 depend on B1" in p for p in _problems(pd, _turn(drop_b1)))
    assert _problems(pd, _turn(drop_b1, {"op": "drop_item", "id": "B2", "reason": "too"})) == []


def test_done_item_cannot_be_dropped(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(item("new:1")))
    with pd.db.backlog_doc() as doc:
        doc["items"][0]["status"] = "done"
    assert any("only an open item" in p for p in
               _problems(pd, _turn({"op": "drop_item", "id": "B1", "reason": "done"})))


def test_supersede_decision_and_binding_rule_sync(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(dec("new:1", binding=True)))
    _approve_all(pd, _turn({"op": "supersede_decision", "id": "D1", "ref": "new:1",
                            "title": "Database", "choice": "Postgres", "reason": REASON,
                            "binding": True}))
    spec = Spec(pd.db.read_spec_doc())
    old, new = spec.decision("D1"), spec.decision("D2")
    assert old.status == "superseded" and old.version == 2 and old.history[0]["superseded_by"] == "D2"
    assert new.supersedes == "D1" and new.status == "accepted"
    assert spec.rule("rule-1").status == "dropped"
    assert [(r.id, r.text) for r in spec.binding_rules()] == [("rule-2", "Database: Postgres")]
    # superseding with a non-binding decision leaves no active rule
    _approve_all(pd, _turn({"op": "supersede_decision", "id": "D2", "ref": "new:1",
                            "title": "Database", "choice": "Either", "reason": REASON}))
    assert Spec(pd.db.read_spec_doc()).binding_rules() == []


def test_decision_rule_not_droppable_directly(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(dec("new:1", binding=True)))
    out = _problems(pd, _turn({"op": "drop_rule", "id": "rule-1", "reason": "nope"}))
    assert any("supersede the decision" in p for p in out)
    d = pd.open_draft()
    pd.merge_pm_turn(d, _turn({"op": "drop_rule", "id": "rule-1", "reason": "nope"}), False)
    pd.set_change(d.id, "c1", True)
    with pytest.raises(PlanDraftError, match="belongs to decision"):
        pd.approve(d.id, "erik")


def test_approval_is_atomic(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req()))
    before = (pd.db.read_spec_doc(), pd.db.read_backlog_doc())
    d = pd.open_draft()
    d = pd.merge_pm_turn(d, _turn(req("new:1", text="Second requirement"),
                                  item("new:2", serves=["new:1"]),
                                  {"op": "edit_item", "id": "B77", "priority": 3}), False)
    for c in d.changes:
        pd.set_change(d.id, c.cid, True)
    with pytest.raises(PlanDraftError, match="B77"):
        pd.approve(d.id, "erik")
    assert (pd.db.read_spec_doc(), pd.db.read_backlog_doc()) == before
    still = pd.get(d.id)
    assert still.status == "open" and still.version is None


def test_undecided_blocks_but_rejected_is_skipped(pd: PlanDrafts) -> None:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(req(), item(serves=["new:1"])), True)
    with pytest.raises(PlanDraftError, match="c1 .*c2"):
        pd.approve(d.id, "erik")
    pd.set_change(d.id, "c1", True)
    pd.set_change(d.id, "c2", False)
    res = pd.approve(d.id, "erik")
    assert res.applied == ["c1"] and "new:2" not in res.created
    assert Backlog(pd.db.project_dir.parent.parent, "p").items() == []


def test_nothing_ticked_and_closed_drafts(pd: PlanDrafts) -> None:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(req()), False)
    pd.set_change(d.id, "c1", False)
    with pytest.raises(PlanDraftError, match="no ticked"):
        pd.approve(d.id, "erik")
    pd.discard(d.id)
    assert pd.get(d.id).status == "discarded" and pd.current() is None
    with pytest.raises(PlanDraftError):
        pd.approve(d.id, "erik")
    with pytest.raises(PlanDraftError):
        pd.discard(d.id)
    with pytest.raises(PlanDraftError):
        pd.set_change(d.id, "c1", True)
    with pytest.raises(PlanDraftError):
        pd.merge_pm_turn(d, _turn(req()), False)
    with pytest.raises(PlanDraftError):
        pd.get("draft-99")
    assert len(pd.all()) == 1


def test_unresolved_ref_fails_cleanly(pd: PlanDrafts) -> None:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(item(serves=["new:5"])), False)
    pd.set_change(d.id, "c1", True)
    with pytest.raises(PlanDraftError, match="new:5"):
        pd.approve(d.id, "erik")


# ── drafts and merging ──────────────────────────────────────────────────────
def test_one_open_draft_and_base_version(pd: PlanDrafts) -> None:
    a = pd.open_draft()
    assert pd.open_draft().id == a.id and a.base_version == 0
    _approve_all(pd, _turn(req()))
    assert pd.current() is None
    assert pd.open_draft().base_version == 1


def test_merge_decision_starts_depend_on_lead_seat(pd: PlanDrafts) -> None:
    t = _turn(req(), dec(), {"op": "supersede_decision", "id": "D1", "ref": "new:3",
                             "title": "Title", "choice": "c", "reason": REASON})
    d = pd.merge_pm_turn(pd.open_draft(), t, lead_held=False)
    assert [c.accepted for c in d.changes] == [None, True, True]
    assert all(c.by == "pm" for c in d.changes)
    pd.discard(d.id)
    d2 = pd.merge_pm_turn(pd.open_draft(), t, lead_held=True)
    assert [c.accepted for c in d2.changes] == [None, None, None]


def test_merge_replaces_untouched_keeps_human_decided(pd: PlanDrafts) -> None:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(req(), dec(), item(serves=["new:1"])), False)
    assert [c.cid for c in d.changes] == ["c1", "c2", "c3"]
    pd.set_change(d.id, "c1", True)          # human ticked the requirement
    pd.set_change(d.id, "c3", False)         # and rejected the item
    d = pd.merge_pm_turn(d, _turn(dec("new:1", choice="Postgres")), False)
    assert [c.cid for c in d.changes] == ["c1", "c3", "c4"]
    assert d.changes[0].accepted is True and d.changes[1].accepted is False
    assert d.changes[2].change["choice"] == "Postgres"
    # the new turn reused ref new:1, which a kept change owns, so it was renamed
    assert d.changes[0].change["ref"] == "new:1" and d.changes[2].change["ref"] == "new:4"


def test_merge_ref_rename_rewrites_references(pd: PlanDrafts) -> None:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(req()), False)
    pd.set_change(d.id, "c1", True)
    d = pd.merge_pm_turn(d, _turn(req("new:1", text="Another requirement"),
                                  item("new:2", serves=["new:1"])), False)
    new = [c.change for c in d.changes[1:]]
    assert new[0]["ref"] == "new:3" and new[1]["serves"] == ["new:3"]
    pd.set_change(d.id, "c2", True)
    pd.set_change(d.id, "c3", True)
    res = pd.approve(d.id, "erik")
    assert res.created["new:1"] == "R1" and res.created["new:3"] == "R2"
    assert Backlog(pd.db.project_dir.parent.parent, "p").get("B1").serves == ["R2"]


def test_touched_decision_is_human_decided(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(dec("new:1"), dec("new:2", title="Cache")), lead_held=True)
    spec = Spec(pd.db.read_spec_doc())
    assert {d.decided_by for d in spec.decisions} == {"human"}
    _approve_all(pd, _turn(dec("new:1", title="Queue")), lead_held=False)
    assert Spec(pd.db.read_spec_doc()).decision("D3").decided_by == "pm"


# ── direct edits ────────────────────────────────────────────────────────────
def test_apply_direct_pins_and_records(pd: PlanDrafts) -> None:
    _approve_all(pd, _turn(req()))
    res = pd.apply_direct(EditRequirement(op="edit_requirement", id="R1", text="Reworded",
                                          priority="could"), "erik")
    assert res.version == 2 and res.applied == ["c1"]
    r = Spec(pd.db.read_spec_doc()).requirement("R1")
    assert r.pinned_fields == ["priority", "text"] and r.text == "Reworded"
    assert r.history[-1]["by"] == "erik"
    d = pd.get("draft-2")
    assert d.status == "approved" and d.changes[0].by == "human"
    # a direct edit works while a PM draft is open, and is not blocked by pins
    pd.merge_pm_turn(pd.open_draft(), _turn(req("new:1", text="Open one")), False)
    pd.apply_direct(EditRequirement(op="edit_requirement", id="R1", priority="must"), "erik")
    assert pd.current() is not None and pd.current().changes[0].accepted is None  # type: ignore[union-attr]


def test_apply_direct_new_objects_are_human(pd: PlanDrafts) -> None:
    res = pd.apply_direct(AddRequirement(op="add_requirement", ref="new:1", text="Fast pages",
                                         kind="quality", priority="should",
                                         checks=["loads under 1s"]), "erik")
    assert res.created == {"new:1": "R1"}
    spec = Spec(pd.db.read_spec_doc())
    assert spec.requirement("R1").source == "human"
    res = pd.apply_direct(AddDecision(op="add_decision", ref="new:1", title="Cache",
                                      choice="Redis", reason=REASON, serves=["R1"]), "erik")
    assert Spec(pd.db.read_spec_doc()).decision("D1").decided_by == "human"
    pd.apply_direct(AddItem(op="add_item", ref="new:1", title="Add caching",
                            serves=["R1"]), "erik")
    assert Backlog(pd.db.project_dir.parent.parent, "p").get("B1").source == "human"


def test_apply_direct_failure_leaves_nothing(pd: PlanDrafts) -> None:
    with pytest.raises(PlanDraftError):
        pd.apply_direct(DropRequirement(op="drop_requirement", id="R9", reason="nope"), "erik")
    assert pd.all() == [] and pd.db.read_spec_doc()["version"] == 0


# ── conversation ────────────────────────────────────────────────────────────
def test_plan_messages(pd: PlanDrafts) -> None:
    pd.add_message("human", "let's build a todo app", None, "spec_first")
    pd.add_message("pm", "Here is a draft", "draft-1", "spec_first")
    msgs = pd.messages(10)
    assert [m["author"] for m in msgs] == ["human", "pm"] and msgs[1]["draft_id"] == "draft-1"
    assert len(pd.messages(1)) == 1


# ── backlog.serves ──────────────────────────────────────────────────────────
def test_backlog_serves_validated_editable_and_default(tmp_path: Path) -> None:
    bl = Backlog(tmp_path, "p")
    it = bl.add("Thing one", serves=["R1", "R2", "R1"])
    assert it.serves == ["R1", "R2"]
    assert bl.update(it.id, serves=["R4"]).serves == ["R4"]
    for bad in (["4"], ["R"], ["r4"], ["R4.1"], "R4", [3]):
        with pytest.raises(BacklogError):
            bl.update(it.id, serves=bad)
        with pytest.raises(BacklogError):
            bl.add("Other thing", serves=bad)  # type: ignore[arg-type]
    assert bl.add("Third thing").serves == []


def test_old_backlog_rows_without_serves_load(tmp_path: Path) -> None:
    d = tmp_path / "projects" / "p"
    d.mkdir(parents=True)
    (d / "backlog.json").write_text(json.dumps({"next": 2, "items": [
        {"id": "B1", "title": "Legacy", "acceptance": [], "kind": "code", "size": "M",
         "priority": 0, "status": "open", "notes": "", "files_hint": [], "depends_on": [],
         "source": "human", "created_at": 1.0, "updated_at": 1.0, "run_id": None,
         "pending": None, "history": []}]}))
    bl = Backlog(tmp_path, "p")
    assert bl.get("B1").serves == [] and bl.get("B1").pinned_fields == []
    assert bl.update("B1", title="Renamed").serves == []
    # and a row stored by an older build directly in the table
    conn = sqlite3.connect(d / "work.db")
    row = BacklogItem(id="B2", title="Direct row").__dict__.copy()
    for key in ("serves", "pinned_fields"):
        row.pop(key)
    conn.execute("INSERT INTO backlog_items(id, num, status, priority, data) "
                 "VALUES ('B2', 2, 'open', 0, ?)", (json.dumps(row),))
    conn.commit()
    conn.close()
    assert bl.get("B2").serves == []


# ── team ops (plan §10.2) ───────────────────────────────────────────────────
TEAM_REASON = "scope now includes a data pipeline"
TEAM = [
    {"role": "builder", "role_type": "builder", "model_tier": "solid", "depends_on": []},
    {"role": "reviewer", "role_type": "reviewer", "model_tier": "solid",
     "depends_on": ["builder"], "review_of": ["builder"]},
]


def add_role(**kw: Any) -> dict[str, Any]:
    return {"op": "add_role", "role": "data_builder", "role_type": "builder",
            "description": "Builds the data pipeline", "model_tier": "solid",
            "depends_on": ["builder"], "review_of": [], "reason": TEAM_REASON, **kw}


def _team_problems(pd: PlanDrafts, *changes: dict[str, Any]) -> list[str]:
    return validate_turn(_turn(*changes), Spec(pd.db.read_spec_doc()),
                         pd.db.read_backlog_doc(), TEAM)


def test_valid_team_ops_pass(pd: PlanDrafts) -> None:
    assert _team_problems(pd, add_role()) == []
    edit = {"op": "edit_role", "role": "reviewer", "review_of": ["builder", "data_builder"],
            "reason": TEAM_REASON}
    assert _team_problems(pd, add_role(), edit) == []


def test_team_op_validation(pd: PlanDrafts) -> None:
    bad = _team_problems(pd, add_role(reason="too short"))
    assert any("reason must be at least 20" in p for p in bad)
    assert any("lowercase slug" in p for p in _team_problems(pd, add_role(role="Data Builder")))
    assert any("already exists" in p for p in _team_problems(pd, add_role(role="builder")))
    assert any("role_type" in p for p in _team_problems(pd, add_role(role_type="wizard")))
    assert any("unknown role" in p for p in _team_problems(
        pd, {"op": "edit_role", "role": "ghost", "description": "x y z", "reason": TEAM_REASON}))
    assert any("unknown role" in p for p in _team_problems(
        pd, {"op": "remove_role", "role": "ghost", "reason": TEAM_REASON}))
    assert any("sets no field" in p for p in _team_problems(
        pd, {"op": "edit_role", "role": "builder", "reason": TEAM_REASON}))
    assert any("not a role" in p for p in _team_problems(pd, add_role(depends_on=["ghost"])))
    assert any("not a role" in p for p in _team_problems(pd, add_role(review_of=["ghost"])))
    assert any("capabilities" in p for p in _team_problems(
        pd, add_role(capabilities={"shell": "everything"})))


def test_team_refs_may_use_roles_added_in_the_same_turn(pd: PlanDrafts) -> None:
    assert _team_problems(pd, add_role(role="a_role", depends_on=["b_role"]),
                          add_role(role="b_role", depends_on=[])) == []
    assert any("cycle" in p for p in _team_problems(
        pd, add_role(role="a_role", depends_on=["b_role"]),
        add_role(role="b_role", depends_on=["a_role"])))


def test_removing_a_depended_on_role(pd: PlanDrafts) -> None:
    team = TEAM + [{"role": "builder2", "role_type": "builder", "depends_on": []}]
    spec, backlog = Spec(pd.db.read_spec_doc()), pd.db.read_backlog_doc()
    turn = _turn({"op": "remove_role", "role": "builder", "reason": TEAM_REASON})
    assert any("still depends" in p for p in validate_turn(turn, spec, backlog, team))
    fixed = _turn({"op": "remove_role", "role": "builder", "reason": TEAM_REASON},
                  {"op": "edit_role", "role": "reviewer", "depends_on": [], "review_of": [],
                   "reason": TEAM_REASON})
    assert validate_turn(fixed, spec, backlog, team) == []


def test_team_must_keep_a_builder(pd: PlanDrafts) -> None:
    assert any("builder" in p for p in _team_problems(
        pd, {"op": "remove_role", "role": "builder", "reason": TEAM_REASON},
        {"op": "edit_role", "role": "reviewer", "depends_on": [], "review_of": [],
         "reason": TEAM_REASON}))
    assert _team_problems(
        pd, {"op": "edit_role", "role": "builder", "role_type": "frontend_builder",
             "reason": TEAM_REASON}) == []


def test_callers_without_a_team_keep_working(pd: PlanDrafts) -> None:
    assert validate_turn(_turn(req()), Spec(pd.db.read_spec_doc()),
                         pd.db.read_backlog_doc()) == []


def test_team_ops_are_never_pre_accepted(pd: PlanDrafts) -> None:
    turn = _turn(dec(), add_role(), {"op": "remove_role", "role": "reviewer",
                                     "reason": TEAM_REASON})
    d = pd.merge_pm_turn(pd.open_draft(), turn, lead_held=False)
    assert [c.accepted for c in d.changes] == [True, None, None]
    d = pd.merge_pm_turn(pd.open_draft(), turn, lead_held=True)
    assert [c.accepted for c in d.changes] == [None, None, None]


def test_approve_returns_ticked_team_changes_in_order(pd: PlanDrafts) -> None:
    edit = {"op": "edit_role", "role": "reviewer", "model_tier": "heavy", "reason": TEAM_REASON}
    res = _approve_all(pd, _turn(add_role(), edit, req()))
    assert [c["op"] for c in res.team_changes] == ["add_role", "edit_role"]
    assert res.team_changes[1] == {"op": "edit_role", "role": "reviewer",
                                   "model_tier": "heavy", "reason": TEAM_REASON}
    assert res.created == {"new:1": "R1"}


def test_rejected_team_changes_are_not_returned(pd: PlanDrafts) -> None:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(add_role(), req()), lead_held=False)
    pd.set_change(d.id, "c1", False)
    pd.set_change(d.id, "c2", True)
    assert pd.approve(d.id, "erik").team_changes == []


def test_add_team_change_from_the_overseer(pd: PlanDrafts) -> None:
    dc = pd.add_team_change(add_role(), by="overseer")
    assert dc.accepted is None and dc.by == "overseer"
    current = pd.current()
    assert current is not None and [c.cid for c in current.changes] == ["c1"]
    with pytest.raises(PlanDraftError):
        pd.add_team_change({"op": "add_requirement"}, by="overseer")
    with pytest.raises(PlanDraftError):
        pd.add_team_change(req(), by="overseer")


# ── reasons (plan §12) ──────────────────────────────────────────────────────
def test_every_change_needs_a_described_reason() -> None:
    from core.plan_drafts import validate_reasons
    raw = {"reply": "ok", "changes": [{"op": "drop_item", "id": "B1", "reason": "merged into B2"}]}
    assert any("set `group`" in p for p in validate_reasons(PlanTurn.model_validate(raw)))
    raw["changes"][0]["group"] = "Tidy"
    assert any("not described" in p for p in validate_reasons(PlanTurn.model_validate(raw)))
    raw["groups"] = [{"name": "Tidy", "summary": "One item fewer.", "because": "B2 covers it.",
                      "source": "person", "quote": "merge B1 into B2 please"}]
    turn = PlanTurn.model_validate(raw)
    assert validate_reasons(turn) == []
    assert validate_reasons(turn, ["Could you MERGE B1 into B2, please?"]) == []
    bad = validate_reasons(turn, ["keep B1"])
    assert len(bad) == 1 and "quote of 'Tidy'" in bad[0]


def test_reasons_reach_history_and_items(pd: PlanDrafts) -> None:
    turn = PlanTurn.model_validate({"reply": "ok", "groups": [
        {"name": "Dash", "summary": "Every run starts with a dash.",
         "because": "The person wants one default ability.", "source": "person",
         "quote": "Default dash with charges"}], "changes": [
        {"op": "add_requirement", "ref": "new:1", "text": "Every run starts with a dash.",
         "kind": "feature", "priority": "must", "checks": ["a new run can dash"],
         "group": "Dash"},
        {"op": "add_item", "ref": "new:2", "title": "Dash ability", "serves": ["new:1"],
         "group": "Dash"}]})
    d = pd.merge_pm_turn(pd.open_draft(), turn, False)
    assert set(d.groups) == {"Dash"} and d.why(d.changes[0])["quote"] == "Default dash with charges"
    for c in d.changes:
        pd.set_change(d.id, c.cid, True)
    res = pd.approve(d.id, "erik")
    spec = Spec(pd.db.read_spec_doc())
    req = spec.requirement(res.created["new:1"])
    assert req.origin and req.origin["why"]["because"] == "The person wants one default ability."
    item = next(i for i in pd.db.read_backlog_doc()["items"] if i["id"] == res.created["new:2"])
    assert item["history"][-1]["change"] == "created"
    assert item["history"][-1]["why"]["name"] == "Dash"
    # A later edit records its own reason on the requirement's history.
    edit = PlanTurn.model_validate({"reply": "ok", "groups": [
        {"name": "Rename", "summary": "The dash gets a clearer name.",
         "because": "Players call it a dash."}], "changes": [
        {"op": "set_title", "id": req.id, "title": "Default dash", "group": "Rename"}]})
    d2 = pd.merge_pm_turn(pd.open_draft(), edit, False)
    pd.set_change(d2.id, d2.changes[0].cid, True)
    pd.approve(d2.id, "erik")
    hist = Spec(pd.db.read_spec_doc()).requirement(req.id).history
    assert hist[-1]["why"]["name"] == "Rename" and hist[-1]["draft"] == d2.id


def test_a_direct_edit_keeps_the_persons_reason(pd: PlanDrafts) -> None:
    from core.plan_drafts import parse_change
    turn = _turn({"op": "add_requirement", "ref": "new:1", "text": "Users can sign in.",
                  "kind": "feature", "priority": "must", "checks": ["login works"]})
    d = pd.merge_pm_turn(pd.open_draft(), turn, False)
    pd.set_change(d.id, d.changes[0].cid, True)
    rid = pd.approve(d.id, "erik").created["new:1"]
    pd.apply_direct(parse_change({"op": "set_title", "id": rid, "title": "Sign in"}), "human",
                    "short names read better")
    hist = Spec(pd.db.read_spec_doc()).requirement(rid).history
    assert hist[-1]["why"]["quote"] == "short names read better"


def test_add_role_names_a_known_contract(pd: PlanDrafts) -> None:
    from core.team_changes import apply_team_changes
    ok = {"op": "add_role", "role": "lead", "role_type": "architect", "model_tier": "heavy",
          "description": "Owns the design.", "contract": "tech_lead", "reason": TEAM_REASON}
    assert any("does not come after the planner lead" in p for p in _team_problems(pd, ok))
    after = {"op": "edit_role", "role": "builder", "depends_on": ["lead"], "reason": TEAM_REASON}
    assert _team_problems(pd, ok, after) == []
    assert any("contract 'chief'" in p for p in _team_problems(pd, {**ok, "contract": "chief"}))
    team, _ = apply_team_changes([], [ok])
    assert team[0]["contract"] == "tech_lead"


def test_set_kind_is_checked_and_applies_to_the_doc(pd: PlanDrafts) -> None:
    from core.team_changes import apply_team_changes, kind_change
    ok = {"op": "set_kind", "kind": "game", "start_mode": "adopt", "reason": TEAM_REASON}
    assert _team_problems(pd, ok) == []
    assert any("kind 'novel'" in p for p in _team_problems(pd, {**ok, "kind": "novel"}))
    assert any("start_mode 'research_first'" in p
               for p in _team_problems(pd, {**ok, "start_mode": "research_first"}))
    team, summary = apply_team_changes([{"role": "b"}], [ok])
    assert team == [{"role": "b"}] and summary["edited"] == []
    assert kind_change([ok]) == {"kind": "game", "start_mode": "adopt"}
    assert kind_change([]) == {}


def test_pm_sets_item_effort_by_difficulty(pd: PlanDrafts) -> None:
    def b1() -> dict[str, Any]:
        return next(r for r in pd.db.read_backlog_doc()["items"] if r["id"] == "B1")

    _approve_all(pd, _turn(item(effort="high")))
    assert b1()["effort"] == "high"
    _approve_all(pd, _turn({"op": "edit_item", "id": "B1", "effort": "low"}))
    assert b1()["effort"] == "low"
    with pytest.raises(ValueError):
        _turn(item(effort="turbo"))


def test_a_tech_lead_replaces_the_planner_in_place(pd: PlanDrafts) -> None:
    """Moving an older project to a tech lead: a second planner is refused, an edit is not."""
    team = [{"role": "planner", "role_type": "planner"},
            {"role": "builder", "role_type": "builder", "depends_on": ["planner"]},
            {"role": "reviewer", "role_type": "reviewer", "is_reviewer": True,
             "depends_on": ["builder"], "review_of": ["builder"]}]
    spec, backlog = Spec(pd.db.read_spec_doc()), pd.db.read_backlog_doc()
    second = _turn({"op": "add_role", "role": "tech_lead", "role_type": "architect",
                    "contract": "tech_lead", "description": "Owns the design.", "model_tier": "solid",
                    "reason": TEAM_REASON})
    assert any("both plan sessions" in p for p in validate_turn(second, spec, backlog, team))
    in_place = _turn({"op": "edit_role", "role": "planner", "role_type": "architect",
                      "contract": "tech_lead", "reason": TEAM_REASON})
    assert validate_turn(in_place, spec, backlog, team) == []


def test_serves_maps_check_ids_to_their_requirement() -> None:
    """An item that names checks (`R2.4`) serves their requirement; duplicates collapse."""
    from core.plan_drafts import AddItem, EditItem
    item = AddItem(op="add_item", ref="new:1", title="Lives", serves=["R2.4", "R2", "R3.10"])
    assert item.serves == ["R2", "R3"]
    assert EditItem(op="edit_item", id="B1", serves=["R5.1"]).serves == ["R5"]
    assert EditItem(op="edit_item", id="B1").serves is None


def test_later_turn_builds_on_the_draft(pd: PlanDrafts) -> None:
    """A follow-up turn keeps earlier proposals and replaces only a revised one."""
    d = pd.merge_pm_turn(pd.open_draft(), _turn(req(), item("new:2", serves=["new:1"]),
                                               {"op": "edit_item", "id": "B1", "priority": 3}), True)
    d = pd.merge_pm_turn(d, _turn({"op": "edit_item", "id": "B1", "priority": 1},
                                  item("new:1", title="Another item")), True)
    ops = [(c.change["op"], c.change.get("ref"), c.change.get("priority")) for c in d.changes]
    assert ("add_requirement", "new:1", "must") in ops
    assert ("add_item", "new:2", None) in ops
    edits = [o for o in ops if o[0] == "edit_item"]
    assert edits == [("edit_item", None, 1)]
    # the new item's clashing ref was renamed, not merged into the kept one
    assert len({c.change.get("ref") for c in d.changes if c.change.get("ref")}) == 3
