"""Tests for core.knowledge: the project knowledge base (role contracts plan §4.3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from core.design import DesignDrafts, DesignTurn
from core.knowledge import (
    KnowledgeNote,
    add_notes,
    build_facts,
    decide_note,
    load_facts,
    mark_stale,
    notes,
    render_for_brief,
)
from core.project_repo import ProjectRepo


def _project_with_code(data_root: Path) -> ProjectRepo:
    repo = ProjectRepo(data_root, "p1")
    base = repo.ensure(None)
    work = data_root / "tmp" / "seed"
    repo.clone_into(work)
    (work / "web" / "src" / "logic").mkdir(parents=True)
    (work / "web" / "src" / "logic" / "game.js").write_text(
        "export function createGame(opts) {}\nexport const step = (state, input) => state;\n")
    (work / "tests").mkdir()
    (work / "tests" / "game.test.js").write_text("test('x', () => {});\n")
    (work / "node_modules").mkdir()
    (work / "node_modules" / "dep.js").write_text("export function nope() {}\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: game"], cwd=work, check=True)
    repo.submit("run_a", work, base)
    repo.accept("run_a")
    return repo


def test_facts_come_from_main(tmp_path: Path) -> None:
    _project_with_code(tmp_path)
    DesignDrafts(tmp_path, "p1").propose(DesignTurn.model_validate({
        "groups": [{"name": "Recover", "summary": "The logic component.",
                    "because": "It owns the rules."}],
        "changes": [{"op": "add_component", "ref": "new:1", "name": "Logic", "group": "Recover",
                     "paths": ["web/src/logic/**"], "responsibility": "Rules of play, pure."}]}),
        by="tech_lead", auto_apply=True)
    facts = build_facts(tmp_path, "p1", check_command="bun test")
    assert facts is not None and load_facts(tmp_path, "p1") == facts
    assert [m["path"] for m in facts["modules"]] == ["web/src/logic/game.js"]
    game = facts["modules"][0]
    assert game["component"] == "C1" and game["exports"] == ["createGame(opts)", "step(state, input)"]
    assert facts["tests"] == ["tests/game.test.js"] and facts["commands"] == ["bun test"]
    assert build_facts(tmp_path, "nope") is None


def test_notes_lifecycle(tmp_path: Path) -> None:
    (tmp_path / "projects" / "p1").mkdir(parents=True)
    out = add_notes(tmp_path, "p1", [
        KnowledgeNote(text="Ghost tests must set mode to the game's mode first.",
                      topic="Testing ghosts", paths=["tests/ghosts.test.js"]),
        KnowledgeNote(text="Run bun test from the project root; there is no build step.",
                      topic="Commands")], by="lead", approve=True)
    assert out.added == ["K1", "K2"]
    proposed = add_notes(tmp_path, "p1", [KnowledgeNote(
        text="Ghost tests: put the ghost outside the house first.", topic="Testing ghosts",
        paths=["tests/ghosts.test.js"], replaces="K1")], by="lead", approve=False)
    assert [n["id"] for n in notes(tmp_path, "p1", ("proposed",))] == proposed.added
    decide_note(tmp_path, "p1", "K3", approve=True, by="human")
    assert {n["id"]: n["status"] for n in notes(tmp_path, "p1", ("approved", "dropped"))} == {
        "K1": "dropped", "K2": "approved", "K3": "approved"}
    assert mark_stale(tmp_path, "p1", ["tests/ghosts.test.js"], "run_b") == ["K3"]
    assert notes(tmp_path, "p1", ("stale",))[0]["stale_since"] == "run_b"


def test_brief_section_filters_by_files_and_trust(tmp_path: Path) -> None:
    facts = {"modules": [{"path": "web/src/logic/game.js", "lines": 900,
                          "exports": ["createGame(opts)"]},
                         {"path": "web/src/render/draw.js", "lines": 600, "exports": ["draw"]}],
             "commands": ["bun test"]}
    rows = [{"id": "K1", "status": "approved", "topic": "Logic", "text": "Logic never draws.",
             "paths": ["web/src/logic/**"], "approved_by": "human"},
            {"id": "K2", "status": "approved", "topic": "Render", "text": "Draw from state only.",
             "paths": ["web/src/render/**"], "approved_by": "lead"},
            {"id": "K3", "status": "approved", "topic": "Commands", "text": "Use bun test.",
             "paths": [], "approved_by": "lead"},
            {"id": "K4", "status": "stale", "topic": "Old", "text": "Out of date.", "paths": []}]
    text = render_for_brief(facts, rows, ["web/src/logic/game.js"])
    assert text is not None
    assert "`web/src/logic/game.js` (900 lines): createGame(opts)" in text
    assert "draw.js" not in text and "K2" not in text and "K4" not in text
    assert "**K1** Logic: Logic never draws." in text                # trusted: a person approved
    assert "<untrusted-" in text and "K3 Commands" in text            # the tech lead approved it
    assert render_for_brief(None, [], ["x.js"]) is None
