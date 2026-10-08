"""Tests for core.design: the design artifact (role contracts plan §4.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.design import (
    Design,
    DesignDrafts,
    DesignError,
    DesignTurn,
    path_matches,
    render_for_brief,
    render_markdown,
    validate_design_turn,
)
from core.work_db import WorkDB

WHY = {"name": "Recover", "summary": "The design as the code has it today.",
       "because": "Nothing was written down before this session.", "evidence": ["web/src"]}


def _turn(*changes: dict[str, Any], groups: list[dict[str, Any]] | None = None) -> DesignTurn:
    return DesignTurn.model_validate({"groups": groups or [WHY],
                                      "changes": [{"group": "Recover", **c} for c in changes]})


LOGIC = {"op": "add_component", "ref": "new:1", "name": "Game logic",
         "paths": ["web/src/logic/**"], "responsibility": "Rules of play, pure and deterministic.",
         "serves": ["R1"]}
RENDER = {"op": "add_component", "ref": "new:2", "name": "Rendering",
          "paths": ["web/src/render/**"], "responsibility": "Draws the game state on a canvas."}
STEP = {"op": "add_interface", "ref": "new:3", "name": "step", "provider": "new:1",
        "signature": "step(input: Input) -> Event[]", "users": ["new:2"]}
FLOW = {"op": "add_flow", "ref": "new:4", "title": "One step of play", "kind": "sequence",
        "diagram": "sequenceDiagram\n  main->>game: step(input)\n  game-->>main: events"}
TD = {"op": "add_tech_decision", "ref": "new:5", "title": "Fixed step", "binding": True,
      "choice": "The logic advances in fixed 60 Hz steps.",
      "reason": "Deterministic tests; a variable step was rejected because replays diverge.",
      "components": ["new:1"]}
DEBT = {"op": "add_debt", "ref": "new:6", "text": "The dash is started by two abilities.",
        "paths": ["web/src/logic/abilities/wall_burst.js"], "components": ["new:1"]}


def _drafts(tmp_path: Path) -> DesignDrafts:
    (tmp_path / "projects" / "p1").mkdir(parents=True)
    return DesignDrafts(tmp_path, "p1")


def test_paths() -> None:
    assert path_matches("web/src/logic/game.js", "web/src/logic/**")
    assert path_matches("web/src/logic", "web/src/logic/**")
    assert not path_matches("web/src/logical.js", "web/src/logic/**")
    assert path_matches("web/src/main.js", "web/src/*.js")
    assert not path_matches("web/src/logic/game.js", "web/src/*.js")
    assert path_matches("web/src/logic/game.js", "web/src/logic")


def test_recover_a_design_and_read_it_back(tmp_path: Path) -> None:
    drafts = _drafts(tmp_path)
    out = drafts.propose(_turn(LOGIC, RENDER, STEP, FLOW, TD, DEBT), by="tech_lead",
                         auto_apply=True, requirement_ids=["R1"])
    assert out.status == "applied" and out.version == 1
    assert out.created == {"new:1": "C1", "new:2": "C2", "new:3": "I1", "new:4": "F1",
                           "new:5": "TD1", "new:6": "DB1"}
    design = Design.load(tmp_path, "p1")
    assert [c.id for c in design.components()] == ["C1", "C2"]
    step = design.interfaces()[0]
    assert (step.provider, step.users) == ("C1", ("C2",))
    assert design.component_of("web/src/logic/abilities/dash.js").id == "C1"  # type: ignore[union-attr]
    assert design.component_of("README.md") is None
    assert design.binding_decisions()[0]["id"] == "TD1"
    origin = design.doc["components"][0]["origin"]
    assert origin["why"]["name"] == "Recover" and origin["by"] == "tech_lead"
    md = render_markdown(design)
    assert "### C1 Game logic" in md and "```mermaid\nsequenceDiagram" in md
    assert "**I1** `step(input: Input) -> Event[]`" in md and "## Known debt" in md


def test_validation_problems(tmp_path: Path) -> None:
    drafts = _drafts(tmp_path)
    drafts.propose(_turn(LOGIC, RENDER, STEP), by="tech_lead", auto_apply=True)
    design = Design.load(tmp_path, "p1")
    bad = _turn(
        {"op": "add_component", "ref": "new:1", "name": "Dash", "paths": ["web/src/logic/**"],
         "responsibility": "Owns the default dash."},
        {"op": "add_component", "ref": "new:2", "name": "Bad", "paths": ["../etc"],
         "responsibility": "Escapes the project."},
        {"op": "add_interface", "ref": "new:3", "name": "not an identifier!", "provider": "C9",
         "signature": "x()"},
        {"op": "add_flow", "ref": "new:4", "title": "Nope", "diagram": "just some prose here"},
        {"op": "add_tech_decision", "ref": "new:5", "title": "Short", "choice": "xyz",
         "reason": "because"},
        {"op": "resolve_debt", "id": "DB7", "how": "it was fixed long ago"})
    problems = validate_design_turn(bad, design, ["R1"])
    text = "\n".join(problems)
    assert "already owned by C1" in text
    assert "'../etc' must be relative" in text
    assert "provider C9 is not an active component" in text
    assert "exported identifier" in text
    assert "must be Mermaid" in text
    assert "reason must be at least 20" in text
    drop = _turn({"op": "drop_component", "id": "C1", "reason": "merged into the new one"})
    assert any("C1 still provides I1" in p for p in validate_design_turn(drop, design))
    assert "unknown debt DB7" in text
    unreasoned = DesignTurn.model_validate({"groups": [], "changes": [{**RENDER, "group": "x"}]})
    assert any("not described" in p for p in validate_design_turn(unreasoned, Design({})))
    with pytest.raises(DesignError, match="already owned"):
        drafts.propose(bad, by="tech_lead", auto_apply=True)


def test_edits_keep_history_and_supersede(tmp_path: Path) -> None:
    drafts = _drafts(tmp_path)
    drafts.propose(_turn(LOGIC, RENDER, STEP, TD), by="tech_lead", auto_apply=True)
    move = {"name": "One dash", "summary": "The dash gets one owner.",
            "because": "Two abilities started their own dash (DB1)."}
    drafts.propose(DesignTurn.model_validate({"groups": [move], "changes": [
        {"op": "edit_interface", "id": "I1", "signature": "step(input: Input, dt?: number)",
         "group": "One dash"},
        {"op": "supersede_tech_decision", "id": "TD1", "ref": "new:1", "title": "Fixed step",
         "choice": "Fixed 60 Hz steps; dt is ignored.", "group": "One dash",
         "reason": "Keeps replays exact; passing real time was rejected again."}]}),
        by="tech_lead", auto_apply=True)
    design = Design.load(tmp_path, "p1")
    assert design.version == 2
    row = design.doc["interfaces"][0]
    assert row["signature"].endswith("dt?: number)") and row["version"] == 2
    assert row["history"][-1]["why"]["name"] == "One dash"
    assert row["history"][-1]["before"]["signature"] == "step(input: Input) -> Event[]"
    tds = {d["id"]: d["status"] for d in design.doc["decisions"]}
    assert tds == {"TD1": "superseded", "TD2": "accepted"}


def test_lead_seat_drafts_wait_for_approval(tmp_path: Path) -> None:
    drafts = _drafts(tmp_path)
    out = drafts.propose(_turn(LOGIC, RENDER), by="tech_lead", auto_apply=False)
    assert out.status == "open" and Design.load(tmp_path, "p1").empty
    assert [d["id"] for d in drafts.open()] == [out.draft_id]
    done = drafts.approve(out.draft_id, "erik")
    assert done.status == "applied" and done.created == {"new:1": "C1", "new:2": "C2"}
    assert not drafts.open()
    with pytest.raises(DesignError, match="not an open design draft"):
        drafts.approve(out.draft_id, "erik")
    second = drafts.propose(_turn(STEP | {"provider": "C1", "users": ["C2"]}), by="tech_lead",
                            auto_apply=False)
    drafts.discard(second.draft_id, "erik")
    assert Design.load(tmp_path, "p1").interfaces() == []


def test_approve_without_some_reasons(tmp_path: Path) -> None:
    drafts = _drafts(tmp_path)
    other = {"name": "Debt", "summary": "Record known debt.", "because": "So it is not forgotten."}
    out = drafts.propose(DesignTurn.model_validate({"groups": [WHY, other], "changes": [
        {**LOGIC, "group": "Recover"}, {**DEBT, "components": [], "group": "Debt"}]}),
        by="tech_lead", auto_apply=False)
    drafts.approve(out.draft_id, "erik", reject_groups=["Debt"])
    design = Design.load(tmp_path, "p1")
    assert [c.id for c in design.components()] == ["C1"] and design.open_debt() == []


def test_brief_section_focuses_on_the_card(tmp_path: Path) -> None:
    drafts = _drafts(tmp_path)
    drafts.propose(_turn(LOGIC, RENDER, STEP, TD), by="tech_lead", auto_apply=True)
    design = Design.load(tmp_path, "p1")
    whole = render_for_brief(design)
    assert whole and "C1 Game logic" in whole and "C2 Rendering" in whole
    render_only = render_for_brief(design, files=["web/src/render/draw.js"])
    assert render_only and "C2 Rendering" in render_only and "C1 Game logic" not in render_only
    assert "**I1** `step(input: Input) -> Event[]` from C1" in render_only
    assert "TD1" in render_only
    assert render_for_brief(Design({})) is None


def test_work_db_keeps_design_separate_from_spec(tmp_path: Path) -> None:
    drafts = _drafts(tmp_path)
    drafts.propose(_turn(LOGIC), by="tech_lead", auto_apply=True)
    db = WorkDB(tmp_path / "projects" / "p1")
    assert db.read_spec_doc()["requirements"] == []
    assert db.read_drafts_doc()["drafts"] == []
    assert len(db.read_design_drafts()) == 1


def test_drift_finds_undeclared_exports(tmp_path: Path) -> None:
    from core.design import drift
    drafts = _drafts(tmp_path)
    drafts.propose(_turn(LOGIC, RENDER, STEP), by="tech_lead", auto_apply=True)
    design = Design.load(tmp_path, "p1")
    found = drift(design, {
        "web/src/logic/game.js": ["step(input)", "createGame(opts)", "PELLET"],
        "web/src/render/draw.js": ["draw(ctx)"],              # C2 declares nothing yet: skipped
        "README.md": ["x"]})
    assert [(d.path, d.name, d.component) for d in found] == [
        ("web/src/logic/game.js", "createGame", "C1"), ("web/src/logic/game.js", "PELLET", "C1")]
    assert "does not declare" in found[0].text()
