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
