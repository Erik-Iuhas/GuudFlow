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
