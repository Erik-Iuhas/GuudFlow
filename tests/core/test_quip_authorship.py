"""Tests for M1 quip-authorship fix — persona-driven voice synthesis fallback.

Covers:
  - _voice_for() returns correct voice for known roles
  - _voice_for() synthesises a voice from profile.json for dynamic roles
  - _voice_for() falls back to system voice when no persona data available
  - _synthesise_from_persona() handles missing profile.json gracefully
  - _synthesise_from_persona() handles malformed profile.json gracefully
  - synthesised voices are cached in _voices after first lookup
  - emit_quip() uses the synthesised voice name
  - hired_personas mapping is consulted when workspace is set
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.quip_engine import AgentVoice, QuipEngine
from core.workspace import Workspace


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_engine(tmp_path: Path) -> QuipEngine:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(text="ok"))
    return QuipEngine(llm=llm, log_path=tmp_path / "quips.json")


def _make_ws_with_persona(role: str, persona_id: str) -> Workspace:
    ws = Workspace(run_id="r1", goal="test", max_iterations=3)
    ws.hired_personas[role] = persona_id
    return ws


def _write_profile(personas_root: Path, persona_id: str, data: dict) -> None:
    persona_dir = personas_root / persona_id
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "profile.json").write_text(json.dumps(data), encoding="utf-8")


# ── _voice_for — known roles ──────────────────────────────────────────────────


def test_voice_for_known_role(tmp_path):
    engine = _make_engine(tmp_path)
    engine.set_voices([AgentVoice(role="developer", name="Dev D", character="methodical")])
    voice = engine._voice_for("developer")
    assert voice.name == "Dev D"
    assert voice.role == "developer"


def test_voice_for_unknown_role_falls_back_to_system(tmp_path):
    engine = _make_engine(tmp_path)
    engine.set_voices([AgentVoice(role="developer", name="Dev D", character="x")])
    voice = engine._voice_for("reviewer")
    # No workspace set — must use fallback (not persona synthesis)
    assert voice.role == "reviewer"
    assert voice.name == "Reviewer"


# ── _synthesise_from_persona ─────────────────────────────────────────────────


def test_synthesise_reads_profile(tmp_path):
    personas_root = tmp_path / "personas"
    _write_profile(personas_root, "p-001", {
        "name": "Aria",
        "character": "Calm and methodical. Never rushes.",
    })
    engine = _make_engine(tmp_path)
    engine._personas_root = personas_root
    voice = engine._synthesise_from_persona("developer", "p-001")
    assert voice is not None
    assert voice.name == "Aria"
    assert "methodical" in voice.character
    assert voice.role == "developer"


def test_synthesise_missing_profile_returns_none(tmp_path):
    personas_root = tmp_path / "personas"
    personas_root.mkdir()
    engine = _make_engine(tmp_path)
    engine._personas_root = personas_root
    voice = engine._synthesise_from_persona("reviewer", "p-does-not-exist")
    assert voice is None


def test_synthesise_malformed_json_returns_none(tmp_path):
    personas_root = tmp_path / "personas"
    persona_dir = personas_root / "p-bad"
    persona_dir.mkdir(parents=True)
    (persona_dir / "profile.json").write_text("NOT VALID JSON", encoding="utf-8")

    engine = _make_engine(tmp_path)
    engine._personas_root = personas_root
    voice = engine._synthesise_from_persona("reviewer", "p-bad")
    assert voice is None


def test_synthesise_partial_profile_uses_defaults(tmp_path):
    personas_root = tmp_path / "personas"
    _write_profile(personas_root, "p-min", {})  # no name, no character
    engine = _make_engine(tmp_path)
    engine._personas_root = personas_root
    voice = engine._synthesise_from_persona("tester", "p-min")
    assert voice is not None
    assert voice.name == "Tester"  # role.capitalize() fallback


# ── _voice_for — workspace lookup path ───────────────────────────────────────


def test_voice_for_dynamic_role_via_workspace(tmp_path):
    personas_root = tmp_path / "personas"
    _write_profile(personas_root, "p-002", {"name": "Byte", "character": "Fast and sharp."})

    ws = _make_ws_with_persona("tech_lead", "p-002")
    engine = _make_engine(tmp_path)
    engine.set_workspace(ws, personas_root)
    engine.set_voices([])  # no pre-built voices

    voice = engine._voice_for("tech_lead")
    assert voice.name == "Byte"
    assert voice.role == "tech_lead"


def test_synthesised_voice_is_cached(tmp_path):
    personas_root = tmp_path / "personas"
    _write_profile(personas_root, "p-003", {"name": "Cache", "character": "..."})

    ws = _make_ws_with_persona("tech_lead", "p-003")
    engine = _make_engine(tmp_path)
    engine.set_workspace(ws, personas_root)
    engine.set_voices([])

    engine._voice_for("tech_lead")  # first lookup
    assert any(v.role == "tech_lead" for v in engine._voices)

    # Second lookup should come from cache — delete profile to prove it
    (personas_root / "p-003" / "profile.json").unlink()
    voice = engine._voice_for("tech_lead")
    assert voice.name == "Cache"


def test_voice_for_no_persona_mapping_falls_back(tmp_path):
    personas_root = tmp_path / "personas"
    ws = Workspace(run_id="r1", goal="test", max_iterations=3)
    # hired_personas is empty — role "escalation_agent" is unknown
    engine = _make_engine(tmp_path)
    engine.set_workspace(ws, personas_root)

    voice = engine._voice_for("escalation_agent")
    # No profile data → fallback voice, role is preserved
    assert voice.role == "escalation_agent"


# ── emit_quip uses synthesised voice ─────────────────────────────────────────


async def test_emit_quip_uses_persona_name(tmp_path):
    personas_root = tmp_path / "personas"
    _write_profile(personas_root, "p-004", {"name": "Nova", "character": "Energetic."})

    ws = _make_ws_with_persona("reviewer", "p-004")
    engine = _make_engine(tmp_path)
    engine.set_workspace(ws, personas_root)
    engine.set_voices([])

    await engine.emit_quip("reviewer", "Looks good to me.", event_type="ask")
    quips = engine.quips()
    assert len(quips) == 1
    assert quips[0].persona == "Nova"
    assert quips[0].role == "reviewer"


async def test_emit_quip_addressed_to_resolves_name(tmp_path):
    personas_root = tmp_path / "personas"
    _write_profile(personas_root, "p-005", {"name": "Orion", "character": "Thorough."})
    _write_profile(personas_root, "p-006", {"name": "Luna", "character": "Decisive."})

    ws = Workspace(run_id="r1", goal="test", max_iterations=3)
    ws.hired_personas["developer"] = "p-005"
    ws.hired_personas["tech_lead"] = "p-006"

    engine = _make_engine(tmp_path)
    engine.set_workspace(ws, personas_root)
    engine.set_voices([])

    await engine.emit_quip("developer", "Need guidance.", addressed_to="tech_lead", event_type="ask")
    quips = engine.quips()
    assert quips[0].persona == "Orion"
    assert "Luna" in quips[0].text  # → Luna appended


# ── set_workspace without personas_root ──────────────────────────────────────


def test_set_workspace_without_root_skips_synthesis(tmp_path):
    ws = _make_ws_with_persona("developer", "p-007")
    engine = _make_engine(tmp_path)
    engine.set_workspace(ws, personas_root=None)  # no root
    engine.set_voices([])

    voice = engine._voice_for("developer")
    # personas_root is None → no synthesis → fallback
    assert voice.role == "developer"
    assert voice.name == "Developer"
