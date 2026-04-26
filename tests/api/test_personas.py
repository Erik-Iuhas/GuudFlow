"""Tests for /api/personas."""

from __future__ import annotations

import json


async def test_list_personas_empty(client):
    r = await client.get("/api/personas")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_personas(client, write_persona):
    write_persona("aria-v1", name="Aria", specialty="React")
    write_persona("kai-v1", name="Kai", specialty="Backend")
    r = await client.get("/api/personas")
    assert r.status_code == 200
    items = r.json()
    assert {p["name"] for p in items} == {"Aria", "Kai"}


async def test_skip_underscore_prefixed(client, write_persona):
    """`data/personas/_example/` is skipped — internal/template."""
    write_persona("_example")
    write_persona("aria-v1")
    r = await client.get("/api/personas")
    items = r.json()
    assert len(items) == 1
    assert items[0]["persona_id"] == "aria-v1"


async def test_get_persona(client, write_persona):
    write_persona("aria-v1", name="Aria")
    r = await client.get("/api/personas/aria-v1")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Aria"
    assert "reputation" in body


async def test_get_persona_unknown(client):
    r = await client.get("/api/personas/ghost")
    assert r.status_code == 404


async def test_persona_episodes(client, write_persona, data_root):
    pdir = write_persona("aria-v1")
    (pdir / "episodes.jsonl").write_text(
        json.dumps({"episode_id": "ep1", "tags": ["x"]}) + "\n"
        + json.dumps({"episode_id": "ep2", "tags": ["y"]}) + "\n",
        encoding="utf-8",
    )
    r = await client.get("/api/personas/aria-v1/episodes")
    assert r.status_code == 200
    eps = r.json()
    assert len(eps) == 2


async def test_persona_episodes_limit(client, write_persona, data_root):
    pdir = write_persona("aria-v1")
    lines = "\n".join(json.dumps({"episode_id": f"ep{i}"}) for i in range(20))
    (pdir / "episodes.jsonl").write_text(lines + "\n", encoding="utf-8")
    r = await client.get("/api/personas/aria-v1/episodes?limit=5")
    assert len(r.json()) == 5


async def test_persona_patterns(client, write_persona, data_root):
    pdir = write_persona("aria-v1")
    (pdir / "patterns.json").write_text(json.dumps({
        "version": 3,
        "patterns": [{"pattern_id": "p1", "title": "t", "lesson": "l"}],
        "anti_patterns": [],
        "archived": [],
    }), encoding="utf-8")
    r = await client.get("/api/personas/aria-v1/patterns")
    assert r.status_code == 200
    assert r.json()["patterns"][0]["pattern_id"] == "p1"


async def test_persona_patterns_default_when_missing(client, write_persona):
    write_persona("aria-v1")
    r = await client.get("/api/personas/aria-v1/patterns")
    assert r.status_code == 200
    body = r.json()
    assert body["patterns"] == []
    assert body["anti_patterns"] == []


# ── Phase F.1.3 — rankings ─────────────────────────────────────────────────


def _seed_training_episodes(
    persona_dir, *, scores: list[tuple[str, float]],
) -> None:
    """Append training episodes with (scenario_id, rubric_score)."""
    lines: list[str] = []
    for i, (scenario_id, score) in enumerate(scores):
        lines.append(json.dumps({
            "episode_id": f"ep_{persona_dir.name}_{i}",
            "run_id": f"r_{persona_dir.name}_{i}",
            "training": True,
            "scenario_id": scenario_id,
            "rubric_score": score,
            "timestamp": f"2026-04-{20-i:02d}T00:00:00+00:00",
        }))
    (persona_dir / "episodes.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8",
    )


async def test_rankings_empty_when_no_training_data(client, write_persona):
    write_persona("aria-v1")
    r = await client.get("/api/personas/rankings")
    assert r.status_code == 200
    assert r.json() == []


async def test_rankings_orders_by_average_score_descending(
    client, write_persona,
):
    a = write_persona("aria-v1", name="Aria", specialty="frontend")
    k = write_persona("kai-v1", name="Kai", specialty="backend")
    n = write_persona("nova-v1", name="Nova", specialty="design")
    _seed_training_episodes(a, scores=[("s1", 0.9), ("s2", 0.85)])
    _seed_training_episodes(k, scores=[("s1", 0.7), ("s2", 0.6)])
    _seed_training_episodes(n, scores=[("s1", 0.95)])

    r = await client.get("/api/personas/rankings")
    assert r.status_code == 200
    body = r.json()
    persona_ids = [row["persona_id"] for row in body]
    assert persona_ids == ["nova-v1", "aria-v1", "kai-v1"]
    assert body[0]["average_score"] == 0.95
    assert body[1]["average_score"] == 0.875
    assert body[1]["n_runs"] == 2


async def test_rankings_filter_by_scenario(client, write_persona):
    """Filter by scenario isolates per-scenario performance — useful
    for the meta-agent's 'best developer for X' query."""
    a = write_persona("aria-v1", name="Aria")
    k = write_persona("kai-v1", name="Kai")
    _seed_training_episodes(a, scores=[("s1", 0.9), ("s2", 0.4)])
    _seed_training_episodes(k, scores=[("s1", 0.6), ("s2", 0.95)])

    r = await client.get("/api/personas/rankings?scenario=s1")
    body = r.json()
    persona_ids = [row["persona_id"] for row in body]
    assert persona_ids == ["aria-v1", "kai-v1"]
    assert body[0]["average_score"] == 0.9


async def test_rankings_filter_by_role_type_substring(client, write_persona):
    """role_type matches against id + specialty as a substring."""
    a = write_persona("aria-frontend-v1", name="Aria", specialty="frontend dev")
    k = write_persona("kai-backend-v1", name="Kai", specialty="backend dev")
    _seed_training_episodes(a, scores=[("s1", 0.9)])
    _seed_training_episodes(k, scores=[("s1", 0.95)])

    r = await client.get("/api/personas/rankings?role_type=frontend")
    body = r.json()
    assert [row["persona_id"] for row in body] == ["aria-frontend-v1"]


async def test_rankings_skips_production_episodes(client, write_persona):
    """Production episodes shouldn't show up in training rankings even
    if they have a rubric_score (which they shouldn't)."""
    a = write_persona("aria-v1")
    (a / "episodes.jsonl").write_text(
        json.dumps({
            "episode_id": "ep_prod",
            "training": False,
            "scenario_id": "s1",
            "rubric_score": 0.99,  # contrived — production ep with score
        }) + "\n",
        encoding="utf-8",
    )
    r = await client.get("/api/personas/rankings")
    assert r.json() == []


async def test_rankings_respects_limit(client, write_persona):
    for i in range(5):
        d = write_persona(f"p{i}-v1")
        _seed_training_episodes(d, scores=[("s1", 0.5 + i * 0.05)])

    r = await client.get("/api/personas/rankings?limit=2")
    body = r.json()
    assert len(body) == 2
    # Descending — top two are p4-v1 (0.7), p3-v1 (0.65).
    assert body[0]["persona_id"] == "p4-v1"
    assert body[1]["persona_id"] == "p3-v1"


# ── Phase F.4 — persona chat endpoint ──────────────────────────────────────


from core.llm import LLMResponse, MockLLMClient, MockTurn


async def test_chat_returns_reply_with_recall_summary(
    client, write_persona, app, auth_header
):
    """Happy path: chat with a persona, get the LLM's reply back along
    with a summary of how much memory was surfaced."""
    write_persona("aria-v1", name="Aria", specialty="frontend")
    app.state.chat_llm = MockLLMClient([MockTurn(text="Hi there.")])

    r = await client.post(
        "/api/personas/aria-v1/chat",
        json={"message": "What did you learn about JWT?"},
        headers=auth_header,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["persona_id"] == "aria-v1"
    assert body["reply"] == "Hi there."
    # No memory seeded → all recall counts are zero, but the field
    # shape is still present so the UI can render it.
    assert body["recall"] == {
        "patterns_used": 0,
        "anti_patterns_used": 0,
        "episodes_used": 0,
    }


async def test_chat_unknown_persona_404(client, auth_header):
    r = await client.post(
        "/api/personas/ghost/chat",
        json={"message": "hi"},
        headers=auth_header,
    )
    assert r.status_code == 404


async def test_chat_empty_message_400(client, write_persona, auth_header):
    write_persona("aria-v1")
    r = await client.post(
        "/api/personas/aria-v1/chat",
        json={"message": "   "},
        headers=auth_header,
    )
    assert r.status_code == 400


async def test_chat_requires_auth(client, write_persona):
    write_persona("aria-v1")
    r = await client.post(
        "/api/personas/aria-v1/chat",
        json={"message": "hi"},
    )
    # require_bearer rejects unauthenticated calls.
    assert r.status_code in (401, 403)


async def test_chat_llm_failure_returns_502(
    client, write_persona, app, auth_header
):
    """When the LLM raises, the route surfaces 502 rather than 500 — chat
    is consultation, the failure is upstream, not in our code."""

    class ExplodingLLM:
        async def complete(self, **_kwargs):  # pragma: no cover — raises
            raise RuntimeError("LLM unreachable")

    write_persona("aria-v1")
    app.state.chat_llm = ExplodingLLM()

    r = await client.post(
        "/api/personas/aria-v1/chat",
        json={"message": "hi"},
        headers=auth_header,
    )
    assert r.status_code == 502


async def test_chat_system_prompt_includes_identity_and_memory(
    client, write_persona, app, auth_header, data_root
):
    """The system prompt sent to the LLM contains the persona's identity
    and a memory block when patterns exist. Captured via a custom LLM
    that records what it was called with."""

    class CapturingLLM:
        def __init__(self):
            self.captured = None

        async def complete(self, system: str, messages, **_kwargs):
            self.captured = {"system": system, "messages": messages}
            return LLMResponse(text="captured", stop_reason="end_turn")

    pdir = write_persona("aria-v1", name="Aria", specialty="frontend",
                        character="methodical")
    # Seed a pattern so memory recall has something to surface.
    (pdir / "patterns.json").write_text(json.dumps({
        "version": 3,
        "patterns": [{
            "pattern_id": "pat_1",
            "title": "Validate JWT issuer",
            "lesson": "Always check the iss claim.",
            "applies_when": ["jwt", "auth"],
            "confidence": 0.8,
            "status": "active",
            "applications": [],
            "lifetime_applications": 5,
            "lifetime_successes": 4,
            "source_episodes": [],
        }],
        "anti_patterns": [],
        "archived": [],
    }), encoding="utf-8")

    cap = CapturingLLM()
    app.state.chat_llm = cap

    r = await client.post(
        "/api/personas/aria-v1/chat",
        json={"message": "What did you learn about jwt auth?"},
        headers=auth_header,
    )
    assert r.status_code == 200, r.text

    system = cap.captured["system"]
    assert "Aria" in system
    assert "frontend" in system
    assert "methodical" in system
    # Memory was surfaced because the message tags overlap with
    # the pattern's applies_when.
    assert "Validate JWT issuer" in system
    # The user message is delivered as the only conversation turn.
    assert len(cap.captured["messages"]) == 1
    assert cap.captured["messages"][0].content == "What did you learn about jwt auth?"

    # Recall summary reflects the surfaced pattern.
    assert r.json()["recall"]["patterns_used"] == 1
