"""Rules files, skills and project files for workers (worker context plan R, E1, G1, C1)."""

from __future__ import annotations

from pathlib import Path

from core.harness.worker_rules import (PLANNER_JOB, ProjectFiles, available_skills, layout,
                                       rules_text, skills_for, where_you_are,
                                       write_project_files, write_skills)
from core.spec import Spec, Stack

PYGAME = Stack(language="Python 3.12", framework="pygame 2.6", test_command="python -m pytest -q",
               test_dir="tests")


def test_every_skill_in_the_library_has_a_description() -> None:
    skills = available_skills()
    assert {"orient", "write-cards", "review-a-card", "close-a-check", "python-pygame"} <= set(skills)
    assert all(len(d) > 20 for d in skills.values())


def test_skills_follow_the_role_and_the_stack() -> None:
    assert skills_for("builder", PYGAME)[-1] == "python-pygame"
    assert "write-cards" in skills_for("planner", PYGAME)
    assert skills_for("reviewer", Stack(language="TypeScript", test_command="bun test")) == [
        "orient", "review-a-card", "typescript-bun"]
    assert skills_for("builder", None, extra=["no-such-skill"])[-1] != "no-such-skill"


def test_write_skills_replaces_old_ones_and_adds_project_skills(tmp_path: Path) -> None:
    project = tmp_path / "project_skills"
    (project / "level-building").mkdir(parents=True)
    (project / "level-building" / "SKILL.md").write_text("---\nname: level-building\n---\nx\n")
    (project / "Bad Name").mkdir()
    (project / "Bad Name" / "SKILL.md").write_text("x")
    dest = write_skills(tmp_path / "tools", ["orient"], project)
    write_skills(tmp_path / "tools", ["orient", "test-first"], project)
    assert sorted(p.name for p in dest.iterdir()) == ["level-building", "orient", "test-first"]


def test_layout_skips_noise_and_caps(tmp_path: Path) -> None:
    for d in ("src/game", "tests", "node_modules/x", ".git", ".guudflow/rules"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "main.py").write_text("x")
    for i in range(20):
        (tmp_path / "src" / f"m{i:02d}.py").write_text("x")
    lines = layout(tmp_path)
    text = "\n".join(lines)
    assert "- src/" in text and "- tests/" in text and "- main.py" in text
    assert "node_modules" not in text and ".git" not in text and ".guudflow" not in text
    assert "- …" in text


def test_where_you_are_names_root_tests_and_check(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "requirements.txt").write_text("pygame==2.6.1\n")
    text = where_you_are(tmp_path, stack=PYGAME, check_command="", sandbox=True)
    assert "`/work`" in text and "`requirements.txt`" in text and "`tests/`" in text
    assert "`python -m pytest -q`" in text
    (tmp_path / "empty").mkdir()
    assert "no files yet" in where_you_are(tmp_path / "empty", stack=None, sandbox=False)


def test_rules_text_puts_job_ground_rules_files_and_skills(tmp_path: Path) -> None:
    text = rules_text(role="tech_lead", name="Pia", role_type="planner", planner=True,
                      ground_rules="### The stack\n- Language: Python", where="You are here.",
                      skills=["orient", "write-cards"], description="Owns the design.",
                      project_notes="Levels live in levels/.")
    assert text.startswith("# GuudFlow rules for Pia (tech_lead)")
    assert PLANNER_JOB.splitlines()[0] in text and "Also, from the team: Owns the design." in text
    assert "Language: Python" in text and ".guudflow/project/SPEC.md" in text
    assert "`write-cards`" in text and "Levels live in levels/." in text
    builder = rules_text(role="dev", name="Bram", role_type="builder", planner=False,
                         ground_rules=None, where="here", skills=[])
    assert "No stack or rules are set" in builder and "You build the card" in builder


def test_project_files_render_spec_design_cards_and_stack(tmp_path: Path) -> None:
    spec = Spec({"requirements": [{"id": "R1", "text": "Score resets", "kind": "feature",
                                   "priority": "must",
                                   "checks": [{"id": "R1.1", "text": "score is 0 at start"}]}],
                 "stack": PYGAME.to_dict()})
    dest = write_project_files(tmp_path, ProjectFiles(spec=spec, design_text=None, cards=[
        {"id": "T1", "title": "Score", "owner": "dev", "column": "todo", "serves": ["R1.1"],
         "depends_on": [], "files": ["game/score.py"], "acceptance": ["R1.1: score is 0"]}]))
    assert "R1.1" in (dest / "SPEC.md").read_text()
    assert "pygame 2.6" in (dest / "STACK.md").read_text()
    cards = (dest / "CARDS.md").read_text()
    assert "## T1 Score" in cards and "serves: R1.1" in cards and "`game/score.py`" in cards
    assert "no design yet" in (dest / "DESIGN.md").read_text()
