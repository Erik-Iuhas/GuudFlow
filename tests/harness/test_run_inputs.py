"""User input reaches opencode roles: input files, desk notes, targeted comments."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.harness.run import HarnessSettings, OpencodeRun
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.signals import Signal, SignalType


def _run(tmp_path: Path, persona_id: str | None = None, seeds: dict | None = None) -> OpencodeRun:
    data = tmp_path / "data"
    project = ProjectConfig(project_id="p", goal="Build a game.", harness="opencode",
                            agents=[AgentConfig(role="builder", persona_id=persona_id)],
                            desk_seeds=seeds or {})
    runner = AgentRunner(project=project, run_id="run1", run_root=data / "runs" / "run1",
                         personas_root=tmp_path / "personas")
    run = OpencodeRun(runner, HarnessSettings(sandbox="host"))
    run.mode = "docker"
    run._roles = {"builder": SimpleNamespace(cfg=project.agents[0], role_type="builder")}  # type: ignore[assignment]
    return run


def test_input_files_are_listed_once_per_session(tmp_path: Path) -> None:
    run = _run(tmp_path)
    inputs = run.runner.run_root / "inputs"
    (inputs / "docs").mkdir(parents=True)
    (inputs / "README.md").write_text("# brief\n")
    (inputs / "docs" / "spec.md").write_text("spec\n")
    (inputs / ".secret").write_text("x\n")
    sections = dict(run._input_sections("builder", None, continuation=False))
    text = sections["Files the human provided"]
    assert "`/inputs/README.md`" in text and "`/inputs/docs/spec.md`" in text
    assert "Read `/inputs/README.md` first" in text and ".secret" not in text
    assert "Files the human provided" not in dict(
        run._input_sections("builder", None, continuation=True))


def _desk_file(folder: Path, name: str, triggers: list[str], body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ntriggers: {json.dumps(triggers)}\n---\n{body}\n")


def test_desk_notes_the_card_triggers_reach_the_role_once(tmp_path: Path) -> None:
    run = _run(tmp_path, persona_id="sol")
    _desk_file(tmp_path / "personas" / "sol" / "desk", "ghosts", ["ghost"],
               "Ghosts never reverse except on a mode change.")
    _desk_file(tmp_path / "personas" / "sol" / "desk", "audio", ["sound"], "Use WebAudio.")
    first = run._desk_notes("builder", "Wire the ghost movement into the game loop")
    assert "Ghosts never reverse" in first and "WebAudio" not in first
    assert "untrusted" not in first               # the human's own notes are instructions
    assert run._desk_notes("builder", "Ghost eating") == ""   # once per session
    run._desk_seen.pop("builder")
    assert "Ghosts never reverse" in run._desk_notes("builder", "Ghost eating")


def test_proposed_desk_seeds_are_written_and_wrapped(tmp_path: Path) -> None:
    seeds = {"sol": [{"name": "palette-rule", "triggers": ["colour"], "kind": "convention",
                      "priority": "high", "body": "Only palette.js defines colours."},
                     {"name": "../escape", "triggers": ["x"], "body": "nope"}]}
    run = _run(tmp_path, persona_id="sol", seeds=seeds)
    run._write_desk_seeds()
    folder = tmp_path / "data" / "projects" / "p" / "persona_desks" / "sol" / "proposed"
    assert [p.name for p in folder.iterdir()] == ["palette-rule.md"]
    notes = run._desk_notes("builder", "Fix the colour of the walls")
    assert "Only palette.js defines colours." in notes and "untrusted" in notes


def test_targeted_comments_reach_only_their_role(tmp_path: Path) -> None:
    run = _run(tmp_path)
    ws = run.workspace
    ws.post_signal(Signal(type=SignalType.USER_COMMENT, summary="for everyone", from_user=True))
    ws.post_signal(Signal(type=SignalType.USER_COMMENT_PRIORITY, summary="only reviewer",
                          from_user=True, priority="high", detail=json.dumps({"to": "reviewer"})))
    assert run._take_human_comments("builder") == ("for everyone",)
    assert run._take_human_comments("reviewer") == ("(priority) only reviewer", "for everyone")
