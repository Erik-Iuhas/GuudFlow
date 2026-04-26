"""Tests for agents/builtin/memory_writer.py — reconciliation pass per §34."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.builtin.memory_writer import MemoryWriter
from agents.persona import (
    AntiPattern,
    Episode,
    Pattern,
    PatternApplication,
    Persona,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_DECLINING,
    STATUS_EMERGING,
    STATUS_SUPERSEDED,
)
from core.workspace import Workspace


def _persona(tmp_path: Path, persona_id: str = "p") -> tuple[Persona, Path]:
    d = tmp_path / "personas" / persona_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "profile.json").write_text(
        json.dumps({"persona_id": persona_id, "name": "X", "specialty": "Y"}),
        encoding="utf-8",
    )
    p = Persona.load(persona_id, tmp_path / "personas")
    p._dir = d
    return p, d


def _ts(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


# ── Episode recording ──────────────────────────────────────────────────────


async def test_records_episode_per_persona(tmp_path: Path):
    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r1", goal="Add JWT auth")
    ws.iteration = 1
    mw = MemoryWriter()
    await mw.on_run_complete(
        workspace=ws, project=None, run_id="r1",
        run_root=tmp_path, personas={"developer": p},
    )
    eps = (d / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(eps) == 1
    ep = json.loads(eps[0])
    assert ep["run_id"] == "r1"
    assert "auth" in ep["tags"] or "jwt" in ep["tags"]


async def test_appends_to_existing_episodes(tmp_path: Path):
    p, d = _persona(tmp_path)
    p.episodes.append(Episode(episode_id="ep_old", timestamp=_ts(30)))
    ws = Workspace(run_id="r2", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r2", run_root=tmp_path,
                             personas={"developer": p})
    lines = (d / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    ids = [json.loads(l)["episode_id"] for l in lines]
    assert "ep_old" in ids


# ── Reputation refresh ─────────────────────────────────────────────────────


async def test_reputation_counts_episodes(tmp_path: Path):
    p, d = _persona(tmp_path)
    p.episodes.append(Episode(episode_id="ep_old", timestamp=_ts(30)))
    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1", run_root=tmp_path,
                             personas={"developer": p})
    rep = json.loads((d / "reputation.json").read_text(encoding="utf-8"))
    assert rep["total_episodes"] == 2  # 1 old + 1 new
    assert rep["production"]["total_runs"] == 2


async def test_training_episodes_counted_separately(tmp_path: Path):
    p, d = _persona(tmp_path)
    p.episodes.append(Episode(episode_id="t1", timestamp=_ts(10), training=True))
    p.episodes.append(Episode(episode_id="t2", timestamp=_ts(5), training=True))
    p.episodes.append(Episode(episode_id="prod1", timestamp=_ts(2), training=False))
    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1", run_root=tmp_path,
                             personas={"developer": p})
    rep = json.loads((d / "reputation.json").read_text(encoding="utf-8"))
    assert rep["training"]["total_runs"] == 2
    # The new episode is production (default).
    assert rep["production"]["total_runs"] == 2


# ── Lifecycle transitions ──────────────────────────────────────────────────


async def test_emerging_to_active_after_5_apps_and_high_confidence(tmp_path: Path):
    p, d = _persona(tmp_path)
    pat = Pattern(
        pattern_id="pat", title="t", lesson="l",
        status=STATUS_EMERGING,
        lifetime_applications=5,
        lifetime_successes=5,
        applications=[
            PatternApplication(run_id=f"r{i}", ts=_ts(i), outcome="succeeded")
            for i in range(5)
        ],
    )
    p.patterns.append(pat)
    ws = Workspace(run_id="r_now", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r_now",
                             run_root=tmp_path, personas={"developer": p})
    out = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    assert out["patterns"][0]["status"] == STATUS_ACTIVE


async def test_active_to_declining_on_consecutive_failures(tmp_path: Path):
    p, _ = _persona(tmp_path)
    pat = Pattern(
        pattern_id="pat", title="t", lesson="l",
        status=STATUS_ACTIVE,
        lifetime_applications=8,
        lifetime_successes=5,
        applications=[
            PatternApplication(run_id=f"r{i}", ts=_ts(20 - i), outcome="succeeded")
            for i in range(5)
        ] + [
            PatternApplication(run_id=f"r{i}", ts=_ts(3 - i), outcome="failed")
            for i in range(3)
        ],
    )
    p.patterns.append(pat)
    ws = Workspace(run_id="r_now", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r_now",
                             run_root=tmp_path, personas={"developer": p})
    assert p.patterns[0].status == STATUS_DECLINING


async def test_user_pinned_pattern_not_transitioned(tmp_path: Path):
    p, _ = _persona(tmp_path)
    pat = Pattern(
        pattern_id="pat", title="t", lesson="l",
        status=STATUS_ACTIVE,
        confidence=0.1,  # would trigger declining
        lifetime_applications=10,
        lifetime_successes=1,
        user_pinned=True,
    )
    p.patterns.append(pat)
    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})
    assert p.patterns[0].status == STATUS_ACTIVE  # untouched


# ── Contradiction reconciliation ───────────────────────────────────────────


async def test_contradiction_supersedes_loser(tmp_path: Path):
    p, _ = _persona(tmp_path)
    p.patterns.extend([
        Pattern(
            pattern_id="winner", title="w", lesson="l",
            applies_when=["react", "state"],
            confidence=0.9,
            status=STATUS_ACTIVE,
            lifetime_applications=5, lifetime_successes=5,
        ),
        Pattern(
            pattern_id="loser", title="l", lesson="m",
            applies_when=["react", "state"],
            confidence=0.4,
            status=STATUS_ACTIVE,
            lifetime_applications=5, lifetime_successes=2,
        ),
    ])
    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})
    by_id = {pt.pattern_id: pt for pt in p.patterns}
    assert by_id["winner"].status == STATUS_ACTIVE
    assert by_id["loser"].status == STATUS_SUPERSEDED
    assert by_id["loser"].superseded_by == "winner"


# ── Persistence ────────────────────────────────────────────────────────────


async def test_writes_atomic_json_files(tmp_path: Path):
    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})
    assert (d / "patterns.json").is_file()
    assert (d / "reputation.json").is_file()
    assert (d / "episodes.jsonl").is_file()


async def test_persona_without_dir_attribute_is_skipped(tmp_path: Path):
    """If runner forgets to set persona._dir, MemoryWriter shouldn't crash."""
    p, _ = _persona(tmp_path)
    delattr(p, "_dir")
    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()
    # Should not raise.
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})
