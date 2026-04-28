"""Tests for persona goal synthesis + caching (M2 §5, spec §9)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.persona import _synthesize_goal, _cache_goal, Persona, PersonaProfile


# ── _synthesize_goal ───────────────────────────────────────────────────────


def _profile(specialty: str = "", character: str = "") -> PersonaProfile:
    return PersonaProfile(
        persona_id="test-v1",
        name="Test",
        specialty=specialty,
        character=character,
    )


def test_synthesize_goal_specialty_and_character():
    p = _profile(specialty="backend", character="Methodical and thorough. Prefers explicit over implicit.")
    goal = _synthesize_goal(p)
    assert len(goal) > 0
    assert len(goal) <= 120
    assert "backend" in goal.lower() or "methodical" in goal.lower()


def test_synthesize_goal_specialty_only():
    p = _profile(specialty="frontend")
    goal = _synthesize_goal(p)
    assert "frontend" in goal.lower()
    assert len(goal) <= 120


def test_synthesize_goal_character_only():
    p = _profile(character="Delivers clean, well-tested code. Never cuts corners.")
    goal = _synthesize_goal(p)
    assert len(goal) > 0
    assert len(goal) <= 120


def test_synthesize_goal_empty_profile():
    p = _profile()
    goal = _synthesize_goal(p)
    assert len(goal) > 0
    assert len(goal) <= 120


def test_synthesize_goal_truncated_to_120():
    p = _profile(
        specialty="backend",
        character="A" * 200,  # very long character description
    )
    goal = _synthesize_goal(p)
    assert len(goal) <= 120


# ── _cache_goal ────────────────────────────────────────────────────────────


def test_cache_goal_writes_to_profile_json(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps({"persona_id": "test-v1", "name": "Test"}), encoding="utf-8")

    _cache_goal(tmp_path, "Ship reliable HTTP endpoints.")

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    assert data["goal"] == "Ship reliable HTTP endpoints."


def test_cache_goal_preserves_existing_fields(tmp_path):
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        json.dumps({"persona_id": "test-v1", "name": "Alice", "specialty": "backend"}),
        encoding="utf-8",
    )

    _cache_goal(tmp_path, "Deliver clean backend code.")

    data = json.loads(profile_path.read_text(encoding="utf-8"))
    assert data["name"] == "Alice"
    assert data["specialty"] == "backend"
    assert data["goal"] == "Deliver clean backend code."


def test_cache_goal_no_profile_json_does_not_crash(tmp_path):
    # No profile.json exists — should not raise
    _cache_goal(tmp_path, "Some goal.")
    # File should now exist
    assert (tmp_path / "profile.json").exists()


def test_cache_goal_silently_ignores_io_error(tmp_path):
    # Make profile.json a directory (unwritable as file)
    (tmp_path / "profile.json").mkdir()
    # Should not raise
    _cache_goal(tmp_path, "Some goal.")


# ── Persona.load() synthesizes goal on first load ─────────────────────────


def test_goal_synthesized_on_first_load(tmp_path):
    """Persona without goal field gets one synthesized and cached back to profile.json."""
    personas_root = tmp_path / "personas"
    persona_dir = personas_root / "aria-v1"
    persona_dir.mkdir(parents=True)

    # Write profile WITHOUT goal field
    (persona_dir / "profile.json").write_text(
        json.dumps({
            "persona_id": "aria-v1",
            "name": "Aria",
            "specialty": "backend",
            "character": "Methodical and precise.",
        }),
        encoding="utf-8",
    )
    for fname in ("episodes.jsonl", "patterns.json", "reputation.json", "style.json"):
        if fname.endswith(".jsonl"):
            (persona_dir / fname).write_text("", encoding="utf-8")
        else:
            (persona_dir / fname).write_text("{}", encoding="utf-8")

    persona = Persona.load("aria-v1", personas_root)
    assert persona.profile.goal != "", "goal must be synthesized when absent"
    assert len(persona.profile.goal) <= 120
    assert "backend" in persona.profile.goal.lower() or "methodical" in persona.profile.goal.lower()

    # Verify the goal was also cached back to disk
    data = json.loads((persona_dir / "profile.json").read_text(encoding="utf-8"))
    assert data.get("goal") == persona.profile.goal


def test_existing_goal_not_overwritten(tmp_path):
    """Persona with a pre-existing goal keeps it unchanged."""
    personas_root = tmp_path / "personas"
    persona_dir = personas_root / "bob-v1"
    persona_dir.mkdir(parents=True)

    (persona_dir / "profile.json").write_text(
        json.dumps({
            "persona_id": "bob-v1",
            "name": "Bob",
            "specialty": "frontend",
            "goal": "Ship accessible UI components.",
        }),
        encoding="utf-8",
    )
    for fname in ("episodes.jsonl", "patterns.json", "reputation.json", "style.json"):
        if fname.endswith(".jsonl"):
            (persona_dir / fname).write_text("", encoding="utf-8")
        else:
            (persona_dir / fname).write_text("{}", encoding="utf-8")

    persona = Persona.load("bob-v1", personas_root)
    assert persona.profile.goal == "Ship accessible UI components."
