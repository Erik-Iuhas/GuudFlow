"""Persona -> opencode agent prompt compiler (plan §5.11).

The snapshot test compares a fully rendered prompt with
`tests/harness/fixtures/persona_prompt_snapshot.txt`. After an intentional prompt
change, regenerate it with:

    GUUDFLOW_UPDATE_SNAPSHOTS=1 python -m pytest tests/harness/test_persona_prompt.py -k snapshot
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agents.base import COT_PRIMER, POSITIVE_FRAMING
from agents.persona import Persona
from core.harness.persona_prompt import PromptLayers, build_agent_prompt, strip_legacy_protocol

SNAPSHOT = Path(__file__).parent / "fixtures" / "persona_prompt_snapshot.txt"


class _Recall:
    def __init__(self, text: str) -> None:
        self.text = text

    def render_for_prompt(self) -> str:
        return self.text


class FakePersona:
    """Duck-typed persona: fixed layers and a recording async recall()."""

    def __init__(self, memory: str = "", has_memory: bool = True) -> None:
        self.memory = memory
        self._has_memory = has_memory
        self.queries: list[str] = []

    def identity_layer(self) -> str:
        return "You are Ada, a senior Python engineer. Careful with edge cases."

    def character_layer(self) -> str:
        return "Methodical and thorough; reads the spec before writing code."

    def stake_layer(self) -> str:
        return "Your code ships to production this week."

    def has_memory(self) -> bool:
        return self._has_memory

    async def recall(self, task: str) -> _Recall:
        self.queries.append(task)
        return _Recall(self.memory)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A project dir under ./data/projects/p1 (loaders use cwd-relative paths)."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "projects" / "p1"
    (root / "persona_rules").mkdir(parents=True)
    (root / "persona_rules" / "_shared.md").write_text("Use type hints.\n", encoding="utf-8")
    (root / "persona_rules" / "ada.md").write_text("Never touch migrations.\n", encoding="utf-8")
    (root / "manifesto.md").write_text("Be kind to reviewers.\n", encoding="utf-8")
    return "p1"


async def _full(project_id: str | None = "p1", persona: Any = None) -> PromptLayers:
    return await build_agent_prompt(
        role="builder", description="Builds features.", base_prompt="Write clean code.",
        persona=persona if persona is not None
        else FakePersona(memory="FROM YOUR EXPERIENCE:\n- lesson"),
        project_id=project_id, persona_id="ada", goal="Build a greeter.",
        teammate_brief="- reviewer: checks work", project_context="Roadmap: ship v1.")


async def test_layer_order_and_framing_last(project: str) -> None:
    layers = await _full(project)
    assert layers.included() == ["identity", "character", "stake", "teammate_brief", "manifesto",
                                 "project_rules", "project_context", "memory", "base", "cot",
                                 "framing"]
    text = layers.render()
    markers = ["You are Ada", "Methodical", "ships to production", "YOUR TEAMMATES",
               "OFFICE RULES", "PROJECT RULES", "PROJECT CONTEXT", "FROM YOUR EXPERIENCE",
               "Write clean code.", COT_PRIMER]
    positions = [text.index(m) for m in markers]
    assert positions == sorted(positions)
    assert text.endswith("Make this something you are proud of.")
    assert text.count(POSITIVE_FRAMING) == 1


def test_framing_is_last_even_if_overridden() -> None:
    layers = PromptLayers(identity="x", framing="something else")
    assert layers.render().endswith(POSITIVE_FRAMING)


async def test_no_persona_role_gets_identity_base_cot_framing() -> None:
    layers = await build_agent_prompt(
        role="builder", description="Builds features.", base_prompt="Write clean code.",
        persona=None, project_id=None, persona_id=None, goal="g")
    assert layers.included() == ["identity", "base", "cot", "framing"]
    assert layers.identity == "You are the builder on a small software team. Builds features."


async def test_project_rules_above_memory(project: str) -> None:
    text = (await _full(project)).render()
    assert text.index("Never touch migrations.") < text.index("FROM YOUR EXPERIENCE")
    assert "Use type hints." in text and "Be kind to reviewers." in text


async def test_memory_absent_when_persona_has_none(project: str) -> None:
    persona = FakePersona(memory="SHOULD NOT APPEAR", has_memory=False)
    layers = await _full(project, persona=persona)
    assert "memory" not in layers.included()
    assert persona.queries == []


async def test_memory_absent_when_recall_is_empty(project: str) -> None:
    layers = await _full(project, persona=FakePersona(memory=""))
    assert "memory" not in layers.included()


async def test_recall_query_includes_goal_description_and_hint() -> None:
    persona = FakePersona(memory="m")
    await build_agent_prompt(
        role="builder", description="Builds features.", base_prompt="", persona=persona,
        project_id=None, persona_id="ada", goal="Build a greeter.", task_hint="card: add greet()")
    assert len(persona.queries) == 1
    for part in ("Build a greeter.", "Builds features.", "card: add greet()"):
        assert part in persona.queries[0]


async def test_real_persona_without_memory_has_no_memory_layer() -> None:
    persona = Persona.empty("ada", "Ada", "Python engineer", character="Thorough.")
    layers = await build_agent_prompt(
        role="builder", description="", base_prompt="Base.", persona=persona,
        project_id=None, persona_id="ada", goal="g")
    assert layers.included() == ["identity", "character", "base", "cot", "framing"]
    assert layers.identity.startswith("You are Ada, a Python engineer.")


async def test_digest_stable_and_sensitive(project: str) -> None:
    a, b = await _full(project), await _full(project)
    assert a.digest() == b.digest()
    assert len(a.digest()) == 16
    b.base = "different"
    assert a.digest() != b.digest()


def test_strip_legacy_protocol_drops_marker_lines() -> None:
    text = ("Keep this.\n"
            "Emit [COMMIT] when done.\n"
            "Use `fs-read` first.\n"
            "Call ask-peer to ask.\n"
            "Also fine.")
    assert strip_legacy_protocol(text) == "Keep this.\nAlso fine."


@pytest.mark.parametrize("marker", ["[COMMIT]", "[VERIFY]", "[ASK]", "[ANSWER]", "[DELTA]",
                                    "[ACTION]", "fs-read", "fs-list", "fs-write", "ask-peer",
                                    "board-message", "assign-task", "decompose-task",
                                    "closes: task:"])
def test_strip_legacy_protocol_each_marker(marker: str) -> None:
    assert strip_legacy_protocol(f"a\nuse {marker} here\nb") == "a\nb"


def test_strip_legacy_protocol_is_case_sensitive() -> None:
    text = "mention [commit] and FS-READ in passing"
    assert strip_legacy_protocol(text) == text


def test_strip_legacy_protocol_collapses_blank_runs() -> None:
    assert strip_legacy_protocol("a\n\n\n\n\nb\n[ASK] x\n\n\n\nc") == "a\n\nb\n\nc"


async def test_legacy_protocol_removed_from_base_prompt() -> None:
    layers = await build_agent_prompt(
        role="r", description="", base_prompt="Do work.\nUse fs-list then fs-read.\nBe brief.",
        persona=None, project_id=None, persona_id=None, goal="g")
    assert layers.base == "Do work.\nBe brief."


async def test_snapshot(project: str) -> None:
    persona = FakePersona(memory=(
        "FROM YOUR EXPERIENCE WITH SIMILAR TASKS:\n\nRelevant lessons:\n"
        "- Edge cases: check empty input first. (3 applications, confidence 0.80)"))
    text = (await _full(project, persona=persona)).render() + "\n"
    if os.environ.get("GUUDFLOW_UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(text, encoding="utf-8")
    assert text == SNAPSHOT.read_text(encoding="utf-8")


def test_board_detail_carries_no_prompt_text() -> None:
    """The board gets only a hash and layer names, never prompt text."""
    layers = PromptLayers(identity="secret memory", memory="secret memory")
    blob = json.dumps({"digest": layers.digest(), "layers": layers.included()})
    assert "secret" not in blob
