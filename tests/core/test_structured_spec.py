"""Structured requirements and field-level draft ops (plan §11.2, W4). No LLM calls."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.backlog import Backlog
from core.plan_drafts import PlanDrafts, PlanTurn, parse_change, validate_turn
from core.spec import Check, Requirement, Spec, derive_title, infer_method, resolve_acceptance


def _turn(*changes: dict[str, Any]) -> PlanTurn:
    return PlanTurn.model_validate({"reply": "ok", "changes": list(changes)})


def _approve_all(pd: PlanDrafts, *changes: dict[str, Any]) -> Any:
    d = pd.merge_pm_turn(pd.open_draft(), _turn(*changes), False)
    for c in d.changes:
        if c.accepted is None:
            pd.set_change(d.id, c.cid, True)
    return pd.approve(d.id, "erik")


def _problems(pd: PlanDrafts, *changes: dict[str, Any]) -> list[str]:
    return validate_turn(_turn(*changes), Spec(pd.db.read_spec_doc()), pd.db.read_backlog_doc())


def _req(pd: PlanDrafts) -> Requirement:
    return Spec(pd.db.read_spec_doc()).requirement("R1")


DASH = {"op": "add_requirement", "ref": "new:1", "title": "Default dash",
        "text": "Every run starts with a dash that passes through and eats ghosts.",
        "kind": "feature", "priority": "must", "group": "Default dash",
        "params": [{"key": "charges", "value": 2, "assumed": True},
                   {"key": "recharge", "value": 10, "unit": "s", "assumed": True}],
        "checks": [{"text": "a new run has 2 charges", "method": "unit",
                    "test": "tests/dash.test.js::starts with charges"},
                   "Manual: dashing through a ghost eats it"]}


@pytest.fixture
def pd(tmp_path: Path) -> PlanDrafts:
    drafts = PlanDrafts(tmp_path, "p")
    _approve_all(drafts, DASH)
    return drafts


# ── reading older rows ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "method"), [
    ("Bun test: a run has exactly 5 levels", "unit"),
    ("Bun test over 200 seeds: offers lean", "unit"),
    ("Manual and bun test: pick screens appear", "unit"),
    ("Manual: in the browser the HUD shows it", "manual"),
    ("Manual screenshot: only palette colours", "visual"),
    ("the boss has 5 HP", None),
])
def test_method_is_inferred_from_older_prefixes(text: str, method: str | None) -> None:
    assert infer_method(text) == method
    assert Check.from_dict({"id": "R1.1", "text": text}).method == method


def test_older_rows_get_a_title_from_their_first_clause() -> None:
    assert derive_title("Run structure: a run is 5 levels. Before level 1 ...") == "Run structure"
    long = derive_title("Five level families with many modifiers that keep going on and on and on")
    assert len(long) <= 60 and long.endswith("…")
    row = {"id": "R3", "text": "Run structure: a run is 5 levels.", "kind": "feature",
           "priority": "must"}
    assert Requirement.from_dict(row).title == "Run structure"


# ── add_requirement with structure ──────────────────────────────────────────

def test_structured_requirement_is_stored(pd: PlanDrafts) -> None:
    r = _req(pd)
    assert r.title == "Default dash" and "eats ghosts" in r.text
    assert [(p.key, p.value, p.unit, p.assumed) for p in r.params] == [
        ("charges", 2, "", True), ("recharge", 10, "s", True)]
    assert [(c.id, c.method, c.test) for c in r.checks] == [
        ("R1.1", "unit", "tests/dash.test.js::starts with charges"), ("R1.2", "manual", None)]
    assert "params: charges = 2 (assumed); recharge = 10 s (assumed)" in Spec(
        pd.db.read_spec_doc()).render()


def test_every_op_takes_a_group() -> None:
    change = parse_change({"op": "set_param", "id": "R1", "key": "charges", "value": 3,
                           "group": "Default dash"})
    assert change.group == "Default dash"


# ── field-level ops ─────────────────────────────────────────────────────────

def test_set_param_changes_one_value_and_keeps_the_rest(pd: PlanDrafts) -> None:
    _approve_all(pd, {"op": "set_param", "id": "R1", "key": "charges", "value": 3})
    r = _req(pd)
    assert r.param("charges").value == 3 and r.param("charges").assumed is True
    assert r.param("recharge").value == 10
    assert r.version == 2 and r.history[-1]["params"][0]["value"] == 2


def test_a_human_set_param_is_confirmed_and_pinned(pd: PlanDrafts) -> None:
    pd.apply_direct(parse_change({"op": "set_param", "id": "R1", "key": "charges", "value": 3}),
                    "erik")
    r = _req(pd)
    assert r.param("charges").assumed is False and "param:charges" in r.pinned_fields
    problems = _problems(pd, {"op": "set_param", "id": "R1", "key": "charges", "value": 4})
    assert problems and "human-set param:charges" in problems[0]
    assert _problems(pd, {"op": "set_param", "id": "R1", "key": "recharge", "value": 8}) == []


def test_title_and_statement_ops(pd: PlanDrafts) -> None:
    _approve_all(pd, {"op": "set_title", "id": "R1", "title": "Dash with charges"},
                 {"op": "set_statement", "id": "R1", "text": "Every run starts with a dash."})
    r = _req(pd)
    assert (r.title, r.text) == ("Dash with charges", "Every run starts with a dash.")
    assert r.checks[0].id == "R1.1"                       # checks untouched


def test_edit_check_keeps_its_id_and_state(pd: PlanDrafts) -> None:
    doc = pd.db.read_spec_doc()
    with pd.db.transaction() as txn:
        txn.spec["requirements"][0]["checks"][0]["state"] = "met"
    _approve_all(pd, {"op": "edit_check", "id": "R1.1", "text": "a new run has 3 charges"})
    c = _req(pd).checks[0]
    assert (c.id, c.text, c.state, c.method) == ("R1.1", "a new run has 3 charges", "met", "unit")
    assert doc["requirements"][0]["checks"][0]["text"] == "a new run has 2 charges"


def test_check_numbers_are_never_reused(pd: PlanDrafts) -> None:
    _approve_all(pd, {"op": "remove_check", "id": "R1.2"})
    _approve_all(pd, {"op": "add_check", "id": "R1",
                      "check": {"text": "charges show as pips", "method": "visual"}})
    assert [c.id for c in _req(pd).checks] == ["R1.1", "R1.3"]


def test_field_op_refusals(pd: PlanDrafts) -> None:
    assert "unknown check R1.9" in _problems(pd, {"op": "edit_check", "id": "R1.9",
                                                    "text": "anything at all"})[0]
    assert "has no param 'speed'" in _problems(pd, {"op": "remove_param", "id": "R1",
                                                     "key": "speed"})[0]
    assert "unknown requirement R7" in _problems(pd, {"op": "set_title", "id": "R7",
                                                       "title": "Nope"})[0]
    _approve_all(pd, {"op": "remove_check", "id": "R1.2"})
    assert "would have no check left" in _problems(pd, {"op": "remove_check", "id": "R1.1"})[0]
    assert _problems(pd, {"op": "remove_check", "id": "R1.1"},
                     {"op": "add_check", "id": "R1", "check": {"text": "replacement"}}) == []


# ── items reference checks ──────────────────────────────────────────────────

def test_items_reference_checks_and_follow_edits(pd: PlanDrafts) -> None:
    _approve_all(pd, {"op": "add_item", "ref": "new:2", "title": "Build the dash",
                      "serves": ["R1"], "acceptance": ["R1.1: old copy", "HUD pips drawn"]})
    item = Backlog(pd.db.project_dir.parent.parent, "p").get("B1")
    assert item.acceptance == ["R1.1", "R1.2", "HUD pips drawn"]
    _approve_all(pd, {"op": "edit_check", "id": "R1.1", "text": "a new run has 3 charges"})
    spec = Spec(pd.db.read_spec_doc())
    assert resolve_acceptance(spec, item.acceptance) == [
        "R1.1: a new run has 3 charges", "R1.2: Manual: dashing through a ghost eats it",
        "HUD pips drawn"]


def test_resolve_refreshes_old_copies_and_marks_removed_checks(pd: PlanDrafts) -> None:
    _approve_all(pd, {"op": "remove_check", "id": "R1.2"})
    spec = Spec(pd.db.read_spec_doc())
    assert resolve_acceptance(spec, ["R1.1: a stale copy", "R1.2: gone now", "own line"]) == [
        "R1.1: a new run has 2 charges", "R1.2: gone now (removed from the spec)", "own line"]


# ── PM-turn validation (W5) ─────────────────────────────────────────────────

def test_pm_may_not_rewrite_text_or_checks_through_edit_requirement(pd: PlanDrafts) -> None:
    out = _problems(pd, {"op": "edit_requirement", "id": "R1", "text": "whole new paragraph"})
    assert out and "set_statement" in out[0]
    assert _problems(pd, {"op": "edit_requirement", "id": "R1", "priority": "should"}) == []


def test_numbers_belong_in_params_when_a_requirement_has_them(pd: PlanDrafts) -> None:
    out = _problems(pd, {"op": "set_statement", "id": "R1",
                         "text": "Every run starts with 3 charges that refill in 8 s."})
    assert out and "(3, 8)" in out[0] and "params" in out[0]
    assert _problems(pd, {"op": "set_statement", "id": "R1",
                          "text": "Every run starts with a few dash charges."}) == []
    plain = dict(DASH, ref="new:2", params=[], text="A run is 5 levels long.")
    assert _problems(pd, plain) == []                  # no params: numbers may stay


def test_titles_are_capped_by_the_schema() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _turn(dict(DASH, title="x" * 61))


def test_prompt_shows_the_shape_and_the_field_ops() -> None:
    from core.pm_prompts import Seats, pm_system_prompt
    text = pm_system_prompt(seats=Seats(), start_mode="prototype_first", phase="plan")
    for needle in ('"params"', "set_param", "edit_check", "`group`", "No tunable numbers"):
        assert needle in text
