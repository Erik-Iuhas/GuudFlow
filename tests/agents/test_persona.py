"""Tests for agents/persona.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.persona import (
    AntiPattern,
    DEFAULT_HALF_LIFE_DAYS,
    Episode,
    MemoryRecall,
    Pattern,
    PatternApplication,
    Persona,
    PersonaProfile,
    RECENCY_FACTOR,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DECLINING,
    STATUS_EMERGING,
    STATUS_SUPERSEDED,
    compute_confidence,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_persona(root: Path, persona_id: str, files: dict[str, str]) -> Path:
    persona_dir = root / persona_id
    persona_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (persona_dir / name).write_text(content, encoding="utf-8")
    return persona_dir


def _profile_dict(persona_id: str = "p1") -> dict:
    return {
        "persona_id": persona_id,
        "name": "Aria",
        "specialty": "React frontend",
        "character": "Methodical and quietly proud of her work.",
        "stake_framing": "Real users will see this. Make it count.",
        "preferred_model": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "tools_required": ["fs:read", "fs:write"],
        "created_at": "2026-04-25T00:00:00Z",
    }


def _ts(days_ago: float, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days_ago)).isoformat()


# ── Profile ─────────────────────────────────────────────────────────────────


def test_profile_from_dict_minimal():
    p = PersonaProfile.from_dict({"persona_id": "p1", "name": "Aria", "specialty": "React"})
    assert p.persona_id == "p1"
    assert p.name == "Aria"
    assert p.specialty == "React"
    assert p.recency_half_life_days == DEFAULT_HALF_LIFE_DAYS


def test_profile_from_dict_legacy_id_field():
    """Old persona files used `id` instead of `persona_id`."""
    p = PersonaProfile.from_dict({"id": "old", "name": "X", "specialty": "X"})
    assert p.persona_id == "old"


def test_profile_preserves_recency_override():
    p = PersonaProfile.from_dict({
        "persona_id": "p1",
        "name": "X",
        "specialty": "X",
        "recency_half_life_days": 30,
    })
    assert p.recency_half_life_days == 30


# ── Empty persona ──────────────────────────────────────────────────────────


def test_empty_persona_has_no_memory():
    p = Persona.empty("p1", "Aria", "React")
    assert p.has_memory() is False
    assert p.episodes == []
    assert p.patterns == []
    assert p.anti_patterns == []


def test_empty_persona_renders_identity_layers():
    p = Persona.empty("p1", "Aria", "React frontend",
                      character="methodical",
                      stake_framing="users matter")
    assert "Aria" in p.identity_layer()
    assert "React frontend" in p.identity_layer()
    assert p.character_layer() == "methodical"
    assert p.stake_layer() == "users matter"


# ── Loader ──────────────────────────────────────────────────────────────────


def test_load_persona_full(tmp_path: Path):
    files = {
        "profile.json": json.dumps(_profile_dict()),
        "episodes.jsonl": json.dumps({
            "episode_id": "ep1", "tags": ["jwt", "auth"], "task_context": "JWT work",
            "what_worked": "use timestamp()", "timestamp": _ts(5), "staleness": "fresh",
        }) + "\n",
        "patterns.json": json.dumps({
            "version": 3,
            "patterns": [{
                "pattern_id": "pat_001",
                "title": "JWT exp",
                "lesson": "Use .timestamp() not timedelta",
                "applies_when": ["jwt", "auth"],
                "confidence": 0.9,
                "status": "active",
                "lifetime_applications": 5,
                "lifetime_successes": 5,
                "applications": [{"run_id": "r1", "ts": _ts(1), "outcome": "succeeded"}],
            }],
            "anti_patterns": [],
            "archived": [],
        }),
        "style.json": json.dumps({
            "tendencies": {"considers_accessibility_unprompted": 0.95},
        }),
        "reputation.json": json.dumps({
            "production": {"total_runs": 12, "completed_runs": 11},
            "training": {"total_runs": 5, "graduation_status": "graduated"},
        }),
        "history.json": json.dumps({"runs": []}),
    }
    _write_persona(tmp_path, "p1", files)

    p = Persona.load("p1", tmp_path)
    assert p.profile.name == "Aria"
    assert len(p.episodes) == 1
    assert len(p.patterns) == 1
    assert p.patterns[0].status == STATUS_ACTIVE
    assert p.style.tendencies["considers_accessibility_unprompted"] == 0.95
    assert p.reputation.production.total_runs == 12
    assert p.reputation.training.graduation_status == "graduated"
    assert p.has_memory() is True


def test_load_persona_missing_optional_files(tmp_path: Path):
    """Only profile.json is required; the rest default to empty."""
    _write_persona(tmp_path, "p1", {"profile.json": json.dumps(_profile_dict())})
    p = Persona.load("p1", tmp_path)
    assert p.episodes == []
    assert p.patterns == []
    assert p.reputation.production.total_runs == 0


def test_load_persona_missing_dir_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        Persona.load("ghost", tmp_path)


def test_load_persona_missing_profile_raises(tmp_path: Path):
    (tmp_path / "p1").mkdir()
    with pytest.raises(FileNotFoundError):
        Persona.load("p1", tmp_path)


def test_load_legacy_flat_reputation(tmp_path: Path):
    """Old reputation.json was flat (production-only). Loader migrates."""
    _write_persona(tmp_path, "p1", {
        "profile.json": json.dumps(_profile_dict()),
        "reputation.json": json.dumps({"total_runs": 8, "completed_runs": 7}),
    })
    p = Persona.load("p1", tmp_path)
    assert p.reputation.production.total_runs == 8
    assert p.reputation.production.completed_runs == 7
    assert p.reputation.training.total_runs == 0


def test_load_episodes_skips_malformed_lines(tmp_path: Path):
    bad_jsonl = (
        json.dumps({"episode_id": "ok", "tags": ["x"], "timestamp": _ts(1)}) + "\n"
        "not json\n"
        "\n"
        + json.dumps({"episode_id": "also_ok", "tags": ["y"], "timestamp": _ts(2)}) + "\n"
    )
    _write_persona(tmp_path, "p1", {
        "profile.json": json.dumps(_profile_dict()),
        "episodes.jsonl": bad_jsonl,
    })
    p = Persona.load("p1", tmp_path)
    ids = [e.episode_id for e in p.episodes]
    assert "ok" in ids
    assert "also_ok" in ids
    assert len(p.episodes) == 2


# ── Recency-weighted confidence ────────────────────────────────────────────


def test_compute_confidence_recent_failures_outweigh_old_successes():
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    apps = (
        [PatternApplication(run_id=f"r_old_{i}", ts=_ts(400, now), outcome="succeeded") for i in range(10)]
        + [PatternApplication(run_id=f"r_new_{i}", ts=_ts(2, now), outcome="failed") for i in range(3)]
    )
    p = Pattern(
        pattern_id="pat", title="t", lesson="l",
        applications=apps,
        lifetime_applications=13,
        lifetime_successes=10,
    )
    conf = compute_confidence(p, half_life=DEFAULT_HALF_LIFE_DAYS, now=now)
    # Recent failures should pull weighted rate well below the lifetime 10/13 = 0.77.
    assert conf < 0.5


def test_compute_confidence_recent_successes_high():
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    apps = [
        PatternApplication(run_id=f"r{i}", ts=_ts(i, now), outcome="succeeded")
        for i in range(5)
    ]
    p = Pattern(
        pattern_id="pat", title="t", lesson="l",
        applications=apps,
        lifetime_applications=5, lifetime_successes=5,
    )
    conf = compute_confidence(p, half_life=DEFAULT_HALF_LIFE_DAYS, now=now)
    assert conf > 0.95


def test_compute_confidence_no_applications_returns_stored():
    p = Pattern(pattern_id="x", title="t", lesson="l", confidence=0.42)
    assert compute_confidence(p, half_life=DEFAULT_HALF_LIFE_DAYS) == 0.42


# ── Recall — basic filtering ────────────────────────────────────────────────


@pytest.fixture
def persona_with_memory() -> Persona:
    now = datetime.now(timezone.utc)
    p = Persona.empty("p1", "Aria", "React frontend")
    p.patterns = [
        Pattern(
            pattern_id="pat_active", title="JWT exp", lesson="use .timestamp()",
            applies_when=["jwt", "auth"],
            confidence=0.9, status=STATUS_ACTIVE,
            applications=[PatternApplication(run_id="r1", ts=now.isoformat(), outcome="succeeded")],
            lifetime_applications=5, lifetime_successes=5,
        ),
        Pattern(
            pattern_id="pat_emerging", title="Refresh tokens", lesson="rotate on use",
            applies_when=["refresh", "token"],
            confidence=0.6, status=STATUS_EMERGING,
            applications=[],
            lifetime_applications=2, lifetime_successes=2,
        ),
        Pattern(
            pattern_id="pat_declining", title="Old session approach", lesson="cookies only",
            applies_when=["session", "auth"],
            confidence=0.4, status=STATUS_DECLINING,
            applications=[PatternApplication(run_id="r2", ts=now.isoformat(), outcome="failed")],
            lifetime_applications=3, lifetime_successes=1,
        ),
        Pattern(
            pattern_id="pat_archived", title="Archived",
            lesson="don't show up",
            applies_when=["jwt"],
            confidence=0.2, status=STATUS_ARCHIVED,
        ),
        Pattern(
            pattern_id="pat_superseded", title="Superseded",
            lesson="don't show up either",
            applies_when=["jwt"],
            confidence=0.5, status=STATUS_SUPERSEDED,
        ),
    ]
    p.anti_patterns = [
        AntiPattern(
            anti_pattern_id="anti_001", title="useReducer for toggles",
            lesson="reviewers flag this",
            alternative="use useState",
            applies_when=["react", "state"],
            status=STATUS_ACTIVE, confidence=0.85,
        ),
    ]
    p.episodes = [
        Episode(
            episode_id="ep_jwt", tags=["jwt", "auth"], task_context="JWT impl",
            what_worked="use .timestamp()", staleness="fresh",
            timestamp=now.isoformat(),
        ),
        Episode(
            episode_id="ep_aged", tags=["jwt"], task_context="aged jwt note",
            staleness="aged", timestamp=now.isoformat(),
        ),
        Episode(
            episode_id="ep_stale", tags=["jwt"], task_context="stale jwt note",
            staleness="stale", timestamp=now.isoformat(),
        ),
        Episode(
            episode_id="ep_unrelated", tags=["css"], task_context="CSS work",
            staleness="fresh", timestamp=now.isoformat(),
        ),
    ]
    return p


async def test_recall_only_returns_prompt_eligible_statuses(persona_with_memory: Persona):
    recall = await persona_with_memory.recall("Implement JWT auth with refresh tokens")
    pattern_ids = {rp.pattern.pattern_id for rp in recall.patterns}
    assert "pat_archived" not in pattern_ids
    assert "pat_superseded" not in pattern_ids
    # Active and emerging ones that match are eligible.
    assert "pat_active" in pattern_ids


async def test_recall_flags_declining_with_caution(persona_with_memory: Persona):
    recall = await persona_with_memory.recall("session auth flow")
    declining = [rp for rp in recall.patterns if rp.pattern.pattern_id == "pat_declining"]
    assert declining, "declining pattern should still be returned"
    assert declining[0].caution is True


async def test_recall_excludes_unrelated_anti_patterns(persona_with_memory: Persona):
    recall = await persona_with_memory.recall("set up python build pipeline")
    # The anti-pattern is React-state-related; this query has no overlap.
    assert recall.anti_patterns == []


async def test_recall_respects_applies_when_excludes():
    p = Persona.empty("p1", "X", "Y")
    p.patterns = [Pattern(
        pattern_id="pat", title="t", lesson="l",
        applies_when=["react"],
        applies_when_excludes=["legacy_refactor"],
        confidence=0.9, status=STATUS_ACTIVE,
    )]
    # query has the exclusion tag → pattern filtered out
    r = await p.recall("react legacy_refactor on existing codebase")
    assert r.patterns == []
    # query without it → present
    r = await p.recall("react component layout")
    assert len(r.patterns) == 1


async def test_recall_episode_staleness_affects_score(persona_with_memory: Persona):
    recall = await persona_with_memory.recall("JWT")
    # The fresh episode should score higher than the aged or stale one.
    by_id = {e.episode_id: score for e, score in recall.episodes}
    assert by_id.get("ep_jwt", 0) > by_id.get("ep_aged", 0) > by_id.get("ep_stale", 0)


async def test_recall_unrelated_query_returns_empty():
    p = Persona.empty("p1", "X", "Y")
    p.patterns = [Pattern(
        pattern_id="pat", title="t", lesson="l",
        applies_when=["jwt"], confidence=0.9, status=STATUS_ACTIVE,
    )]
    r = await p.recall("xyzzy unrelated task")
    assert r.patterns == []


async def test_recall_respects_limit(persona_with_memory: Persona):
    r = await persona_with_memory.recall("jwt auth", limit=1)
    assert len(r.patterns) <= 1


# ── MemoryRecall.render_for_prompt ──────────────────────────────────────────


async def test_render_for_prompt_includes_active_and_caution_marker(persona_with_memory: Persona):
    recall = await persona_with_memory.recall("jwt auth session")
    text = recall.render_for_prompt()
    assert "JWT exp" in text
    # Caution marker for declining pattern
    assert "⚠" in text or "apply with care" in text.lower()
    # Tells the agent there are episodes available
    assert "episode" in text.lower()


def test_render_for_prompt_empty_returns_empty():
    assert MemoryRecall().render_for_prompt() == ""


def test_render_for_prompt_anti_pattern_includes_alternative():
    p = Persona.empty("p1", "X", "Y")
    p.anti_patterns = [AntiPattern(
        anti_pattern_id="a1", title="useReducer for toggles",
        lesson="too much", alternative="use useState",
        applies_when=["react"], status=STATUS_ACTIVE, confidence=0.9,
    )]
    import asyncio
    recall = asyncio.run(p.recall("react state"))
    text = recall.render_for_prompt()
    assert "useReducer for toggles" in text
    assert "use useState" in text
