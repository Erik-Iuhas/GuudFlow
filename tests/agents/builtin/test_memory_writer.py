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


# ── Phase F.3.1 — pattern distillation ─────────────────────────────────────


from core.llm import MockLLMClient, MockTurn, ToolCall


def _seed_episodes(persona: Persona, count: int, tags: list[str]) -> None:
    """Seed `count` episodes that all share the given tags."""
    for i in range(count):
        persona.episodes.append(Episode(
            episode_id=f"ep_seed_{i}",
            run_id=f"run_seed_{i}",
            task_context=f"task {i}",
            what_i_tried="implementation",
            tags=list(tags),
            timestamp=_ts(i + 1),
        ))


async def test_distillation_skipped_when_llm_is_none(tmp_path: Path):
    """Without an LLM, the writer never produces new patterns even with
    a strong cluster."""
    p, d = _persona(tmp_path)
    _seed_episodes(p, 4, ["jwt", "auth"])
    assert len(p.patterns) == 0

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()  # llm=None
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    patterns = json.loads((d / "patterns.json").read_text(encoding="utf-8"))["patterns"]
    assert patterns == []


async def test_distillation_emits_pattern_for_strong_cluster(tmp_path: Path):
    """Cluster of 4 episodes sharing two tags + LLM emits pattern → new
    emerging pattern lands with confidence 0.6 and source episodes
    cited."""
    p, d = _persona(tmp_path)
    _seed_episodes(p, 4, ["jwt", "auth"])

    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="t1",
        name="emit_pattern",
        arguments={
            "title": "Verify JWT issuer",
            "lesson": "Always validate the iss claim on incoming JWTs.",
            "applies_when": ["jwt", "auth"],
        },
    )])])

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    patterns = json.loads((d / "patterns.json").read_text(encoding="utf-8"))["patterns"]
    assert len(patterns) == 1
    assert patterns[0]["title"] == "Verify JWT issuer"
    assert patterns[0]["status"] == STATUS_EMERGING
    assert patterns[0]["confidence"] == 0.6
    assert sorted(patterns[0]["applies_when"]) == ["auth", "jwt"]
    # source_episodes covers the cluster (≥3 of the seeded ids).
    assert len(patterns[0]["source_episodes"]) >= 3


async def test_distillation_emit_no_pattern_adds_nothing(tmp_path: Path):
    p, d = _persona(tmp_path)
    _seed_episodes(p, 4, ["jwt", "auth"])

    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="t1",
        name="emit_no_pattern",
        arguments={"reason": "outcomes mixed, no clear lesson"},
    )])])

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    patterns = json.loads((d / "patterns.json").read_text(encoding="utf-8"))["patterns"]
    assert patterns == []


async def test_distillation_llm_failure_does_not_break_run(tmp_path: Path):
    """Empty MockLLMClient queue raises AssertionError on call. The
    writer must catch and continue, leaving the rest of the pipeline
    intact (episode written, reputation updated)."""
    p, d = _persona(tmp_path)
    _seed_episodes(p, 4, ["jwt", "auth"])

    llm = MockLLMClient([])  # empty — will raise on first call

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    # No patterns synthesized.
    patterns = json.loads((d / "patterns.json").read_text(encoding="utf-8"))["patterns"]
    assert patterns == []
    # But episode was still recorded and reputation refreshed.
    eps = (d / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(eps) >= 1
    rep = json.loads((d / "reputation.json").read_text(encoding="utf-8"))
    assert rep["total_episodes"] >= 1


async def test_distillation_skipped_when_cluster_below_threshold(tmp_path: Path):
    """Only 2 episodes share a tag pair → no LLM call (would have raised
    on empty queue if it had been called)."""
    p, d = _persona(tmp_path)
    _seed_episodes(p, 2, ["jwt", "auth"])

    llm = MockLLMClient([])  # empty — proves the LLM was not called

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    # No assertion error means the LLM was not called.
    assert llm.calls == []


async def test_distillation_dedupes_against_existing_pattern(tmp_path: Path):
    """LLM returns a lesson identical to an existing pattern → drop."""
    p, d = _persona(tmp_path)
    _seed_episodes(p, 4, ["jwt", "auth"])
    p.patterns.append(Pattern(
        pattern_id="pat_existing",
        title="Old title",
        lesson="Always validate the iss claim on incoming JWTs.",
        applies_when=["jwt"],
        status=STATUS_ACTIVE,
        confidence=0.8,
        first_observed=_ts(60),
    ))

    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="t1",
        name="emit_pattern",
        arguments={
            "title": "Verify JWT issuer",
            "lesson": "Always validate the iss claim on incoming JWTs.",
            "applies_when": ["jwt"],
        },
    )])])

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    patterns = json.loads((d / "patterns.json").read_text(encoding="utf-8"))["patterns"]
    # Original survives, no duplicate added.
    assert len(patterns) == 1
    assert patterns[0]["pattern_id"] == "pat_existing"


async def test_distillation_skips_episodes_already_covered(tmp_path: Path):
    """Episodes already cited as source of an active pattern are NOT
    re-clustered — the LLM has already extracted whatever lesson lived
    in them."""
    p, d = _persona(tmp_path)
    _seed_episodes(p, 4, ["jwt", "auth"])
    # Pattern claims all seeded episodes as its evidence already.
    p.patterns.append(Pattern(
        pattern_id="pat_existing",
        title="Existing JWT pattern",
        lesson="Existing lesson",
        applies_when=["jwt"],
        status=STATUS_ACTIVE,
        confidence=0.8,
        source_episodes=[f"ep_seed_{i}" for i in range(4)],
        first_observed=_ts(60),
    ))

    llm = MockLLMClient([])  # empty — proves no LLM call

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    assert llm.calls == []


# ── Phase F.3.3 — deterministic style drift ───────────────────────────────


from core.signals import Signal, SignalType


def _commit_signal(role: str, summary: str = "feat: x") -> Signal:
    return Signal(type=SignalType.GIT_COMMIT, summary=summary, from_agent=role)


async def test_style_drift_incremental_commits_up(tmp_path: Path):
    """Multiple commits by role → incremental_commits drifts up from
    neutral 0.5 toward 1.0."""
    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r1", goal="x")
    ws.iteration = 1
    ws.board.append(_commit_signal("developer", "feat: a"))
    ws.board.append(_commit_signal("developer", "feat: b"))
    ws.board.append(_commit_signal("developer", "feat: c"))

    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    style = json.loads((d / "style.json").read_text(encoding="utf-8"))
    # 0.5 + 0.1 * (1.0 - 0.5) = 0.55
    assert style["tendencies"]["incremental_commits"] == 0.55


async def test_style_drift_single_commit_down(tmp_path: Path):
    """Exactly one commit → incremental_commits drifts down toward 0.0."""
    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r1", goal="x")
    ws.iteration = 1
    ws.board.append(_commit_signal("developer"))

    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    style = json.loads((d / "style.json").read_text(encoding="utf-8"))
    # 0.5 + 0.1 * (0.0 - 0.5) = 0.45
    assert style["tendencies"]["incremental_commits"] == 0.45


async def test_style_drift_iteration_above_one_means_iterates(tmp_path: Path):
    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r1", goal="x")
    ws.iteration = 3  # signals revision happened

    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    style = json.loads((d / "style.json").read_text(encoding="utf-8"))
    assert style["tendencies"]["iterates_on_feedback"] == 0.55


async def test_style_drift_tests_referenced_when_commit_mentions_test(tmp_path: Path):
    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r1", goal="x")
    ws.iteration = 1
    ws.board.append(_commit_signal("developer", "test: add unit tests for greet()"))

    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    style = json.loads((d / "style.json").read_text(encoding="utf-8"))
    assert style["tendencies"]["tests_referenced"] == 0.55


async def test_style_drift_compounds_across_runs(tmp_path: Path):
    """Two consecutive runs with the same incremental signal pull the
    tendency further from neutral than one run."""
    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r1", goal="x")
    ws.iteration = 1
    ws.board.append(_commit_signal("developer"))
    ws.board.append(_commit_signal("developer"))

    mw = MemoryWriter()
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    # Reload persona to simulate a fresh run.
    p2 = Persona.load(p.profile.persona_id, tmp_path / "personas")
    p2._dir = d
    ws2 = Workspace(run_id="r2", goal="x")
    ws2.iteration = 1
    ws2.board.append(_commit_signal("developer"))
    ws2.board.append(_commit_signal("developer"))
    await mw.on_run_complete(workspace=ws2, project=None, run_id="r2",
                             run_root=tmp_path, personas={"developer": p2})

    style = json.loads((d / "style.json").read_text(encoding="utf-8"))
    # 0.5 → 0.55 → 0.595 (round to 4 decimals)
    assert style["tendencies"]["incremental_commits"] == 0.595


# ── Phase F.3.2 — pattern→anti-pattern flip detection ─────────────────────


def _failing_pattern(pattern_id: str, failures: int = 6) -> Pattern:
    """Build a pattern with `failures` consecutive failed applications.
    The flip threshold is 5, so 6 ensures candidacy."""
    apps = [
        PatternApplication(run_id=f"r_{i}", ts=_ts(failures - i), outcome="failed")
        for i in range(failures)
    ]
    return Pattern(
        pattern_id=pattern_id,
        title="useReducer for component state",
        lesson="Reach for useReducer when state has multiple sub-fields.",
        applies_when=["react"],
        confidence=0.18,
        status=STATUS_DECLINING,
        applications=apps,
        lifetime_applications=20,
        lifetime_successes=2,
        source_episodes=["ep_src_1", "ep_src_2", "ep_src_3"],
        first_observed=_ts(180),
    )


async def test_flip_skipped_when_llm_is_none(tmp_path: Path):
    """Without an LLM, no flip can happen — the pattern proceeds through
    the deterministic lifecycle transition and archives. No anti-pattern
    is ever created without an LLM to name the alternative."""
    p, d = _persona(tmp_path)
    p.patterns.append(_failing_pattern("pat_flip_candidate"))
    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter()  # llm=None
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    payload = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    assert payload["anti_patterns"] == []
    # Lifecycle transition runs deterministically; the pattern archives
    # because of low confidence + 3+ recent failures.
    assert payload["patterns"][0]["status"] == STATUS_ARCHIVED


async def test_flip_creates_anti_pattern_on_emit_flip(tmp_path: Path):
    p, d = _persona(tmp_path)
    p.patterns.append(_failing_pattern("pat_flip_candidate"))

    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="t1",
        name="emit_flip",
        arguments={
            "lesson": "Reviewers consistently flag useReducer for non-form state as over-engineering.",
            "alternative": "Use useState directly; reach for useReducer only for true reducer-like state machines.",
        },
    )])])

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    payload = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    # Original pattern is now superseded with a link.
    assert len(payload["patterns"]) == 1
    assert payload["patterns"][0]["status"] == STATUS_SUPERSEDED
    assert payload["patterns"][0]["superseded_by"] is not None
    # New anti-pattern carries the alternative + flipped_from link.
    assert len(payload["anti_patterns"]) == 1
    anti = payload["anti_patterns"][0]
    assert anti["flipped_from"] == "pat_flip_candidate"
    assert anti["flipped_at"]
    assert "useState" in anti["alternative"]
    # Linkage is bidirectional.
    assert payload["patterns"][0]["superseded_by"] == anti["anti_pattern_id"]


async def test_flip_no_flip_keeps_pattern(tmp_path: Path):
    p, d = _persona(tmp_path)
    p.patterns.append(_failing_pattern("pat_flip_candidate"))

    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="t1",
        name="emit_no_flip",
        arguments={"reason": "no clear alternative emerged from the failures"},
    )])])

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    payload = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    assert payload["anti_patterns"] == []
    # When the LLM declines to flip, the pattern still proceeds through
    # the deterministic lifecycle transition — same outcome as no LLM.
    assert payload["patterns"][0]["status"] == STATUS_ARCHIVED


async def test_flip_skipped_below_failure_streak(tmp_path: Path):
    """Pattern with 4 consecutive failures (< 5 threshold) → no LLM call."""
    p, d = _persona(tmp_path)
    p.patterns.append(_failing_pattern("pat_short", failures=4))

    llm = MockLLMClient([])  # empty — would raise if called

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    assert llm.calls == []


async def test_flip_user_pinned_pattern_not_flipped(tmp_path: Path):
    p, d = _persona(tmp_path)
    pinned = _failing_pattern("pat_pinned")
    pinned.user_pinned = True
    p.patterns.append(pinned)

    llm = MockLLMClient([])  # empty — would raise if called

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    assert llm.calls == []


async def test_flip_llm_failure_does_not_break_run(tmp_path: Path):
    p, d = _persona(tmp_path)
    p.patterns.append(_failing_pattern("pat_flip_candidate"))

    llm = MockLLMClient([])  # empty — raises if reached, writer must catch

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    payload = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    assert payload["anti_patterns"] == []
    # Episode still recorded — the run-level pipeline kept working.
    eps = (d / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(eps) >= 1


# ── Phase F.3.4 — synthesis integration over three runs ──────────────────


async def test_three_run_synthesis_loop(tmp_path: Path):
    """Three consecutive runs of the same persona on JWT-flavoured tasks.

    Runs 1 and 2 each record an episode; the LLM is not called because
    the cluster threshold (3 episodes) hasn't been reached. Run 3 hits
    the threshold and the LLM emits a pattern. After run 3 the
    persona's patterns.json should contain a new emerging pattern that
    cites all three episodes.

    This proves the moving parts work together: episode capture →
    cluster detection → LLM call → pattern persistence → reload.
    """
    p, d = _persona(tmp_path)
    persona_id = p.profile.persona_id

    # The LLM is queued with one tool call — only run 3 should reach it.
    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall(
        id="t_run3",
        name="emit_pattern",
        arguments={
            "title": "Validate JWT issuer claim",
            "lesson": "Always check the iss claim against the configured issuer.",
            "applies_when": ["jwt", "auth"],
        },
    )])])

    mw = MemoryWriter(llm=llm)

    async def _run_once(run_id: str, goal: str) -> None:
        # Reload from disk each run so the test exercises the
        # serialised state, not in-memory mutation.
        persona = Persona.load(persona_id, tmp_path / "personas")
        persona._dir = d
        ws = Workspace(run_id=run_id, goal=goal)
        ws.iteration = 1
        await mw.on_run_complete(
            workspace=ws, project=None, run_id=run_id,
            run_root=tmp_path, personas={"developer": persona},
        )

    await _run_once("r1", "Add JWT auth middleware")
    after_run1 = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    assert after_run1["patterns"] == []  # only 1 episode
    assert llm.calls == []

    await _run_once("r2", "Refresh JWT token endpoint")
    after_run2 = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    assert after_run2["patterns"] == []  # only 2 episodes
    assert llm.calls == []

    await _run_once("r3", "JWT auth header parsing")
    after_run3 = json.loads((d / "patterns.json").read_text(encoding="utf-8"))
    assert len(after_run3["patterns"]) == 1
    assert len(llm.calls) == 1

    pat = after_run3["patterns"][0]
    assert pat["status"] == STATUS_EMERGING
    assert pat["confidence"] == 0.6
    assert "iss claim" in pat["lesson"]
    # source_episodes covers all three runs' episodes (the cluster
    # they all shared).
    assert len(pat["source_episodes"]) == 3

    # Episodes file has all three lines.
    episodes = (d / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(episodes) == 3

    # Style drifted across three runs (incremental_commits = false on
    # all three since no commits posted, so trends down from 0.5).
    style = json.loads((d / "style.json").read_text(encoding="utf-8"))
    assert style["tendencies"]["incremental_commits"] < 0.5


async def test_distillation_caps_at_three_clusters(tmp_path: Path):
    """Many candidate clusters → at most MAX_DISTILL_CALLS_PER_RUN LLM
    calls. Caps token spend on chatty personas."""
    p, d = _persona(tmp_path)
    # Seed 5 distinct clusters by tag-pair.
    pairs = [
        ["jwt", "auth"],
        ["redis", "cache"],
        ["sql", "migration"],
        ["graphql", "schema"],
        ["docker", "deploy"],
    ]
    for i, tags in enumerate(pairs):
        for j in range(4):
            p.episodes.append(Episode(
                episode_id=f"ep_p{i}_{j}",
                run_id=f"r_{i}_{j}",
                task_context=f"task {i}-{j}",
                tags=list(tags),
                timestamp=_ts(i * 10 + j + 1),
            ))

    # Queue 3 emit_no_pattern responses; if a 4th call happens the queue
    # is empty and will raise → but the writer catches it, so we just
    # measure llm.calls afterward.
    llm = MockLLMClient([
        MockTurn(tool_calls=[ToolCall(
            id=f"t{i}", name="emit_no_pattern", arguments={"reason": "n/a"}
        )])
        for i in range(3)
    ])

    ws = Workspace(run_id="r1", goal="x")
    mw = MemoryWriter(llm=llm)
    await mw.on_run_complete(workspace=ws, project=None, run_id="r1",
                             run_root=tmp_path, personas={"developer": p})

    assert len(llm.calls) == 3


# ── Phase F.1.3 — graduation logic ─────────────────────────────────────────


from agents.builtin.memory_writer import _compute_graduation_status


def _training_episode(
    *, scenario_id: str, rubric_score: float, ts_offset_days: int = 0,
) -> Episode:
    return Episode(
        episode_id=f"ep_{scenario_id}_{rubric_score}_{ts_offset_days}",
        run_id=f"r_{scenario_id}_{ts_offset_days}",
        training=True,
        scenario_id=scenario_id,
        rubric_score=rubric_score,
        timestamp=_ts(ts_offset_days),
    )


def test_graduation_below_window_size_is_ungraduated():
    """Fewer than 5 training episodes → never graduated, regardless of
    how good the scores are."""
    eps = [
        _training_episode(scenario_id="a", rubric_score=1.0),
        _training_episode(scenario_id="b", rubric_score=1.0),
        _training_episode(scenario_id="c", rubric_score=1.0),
    ]
    assert _compute_graduation_status(eps) == "ungraduated"


def test_graduation_below_three_distinct_scenarios_is_ungraduated():
    eps = [
        _training_episode(scenario_id="a", rubric_score=0.9, ts_offset_days=i)
        for i in range(5)
    ]
    assert _compute_graduation_status(eps) == "ungraduated"


def test_graduation_below_avg_threshold_is_ungraduated():
    eps = [
        _training_episode(scenario_id="a", rubric_score=0.5, ts_offset_days=10),
        _training_episode(scenario_id="b", rubric_score=0.6, ts_offset_days=8),
        _training_episode(scenario_id="c", rubric_score=0.5, ts_offset_days=6),
        _training_episode(scenario_id="a", rubric_score=0.6, ts_offset_days=4),
        _training_episode(scenario_id="b", rubric_score=0.5, ts_offset_days=2),
    ]
    assert _compute_graduation_status(eps) == "ungraduated"


def test_graduation_meets_all_criteria():
    eps = [
        _training_episode(scenario_id="a", rubric_score=0.8, ts_offset_days=10),
        _training_episode(scenario_id="b", rubric_score=0.9, ts_offset_days=8),
        _training_episode(scenario_id="c", rubric_score=0.7, ts_offset_days=6),
        _training_episode(scenario_id="a", rubric_score=0.8, ts_offset_days=4),
        _training_episode(scenario_id="b", rubric_score=0.85, ts_offset_days=2),
    ]
    assert _compute_graduation_status(eps) == "graduated"


def test_graduation_uses_last_5_by_timestamp():
    """Old bad episodes shouldn't drag down a recently-improving
    persona — only the last 5 by timestamp count toward the avg."""
    eps = [
        _training_episode(scenario_id="a", rubric_score=0.1, ts_offset_days=100),
        _training_episode(scenario_id="b", rubric_score=0.1, ts_offset_days=99),
        _training_episode(scenario_id="c", rubric_score=0.1, ts_offset_days=98),
        _training_episode(scenario_id="a", rubric_score=0.9, ts_offset_days=10),
        _training_episode(scenario_id="b", rubric_score=0.9, ts_offset_days=8),
        _training_episode(scenario_id="c", rubric_score=0.9, ts_offset_days=6),
        _training_episode(scenario_id="a", rubric_score=0.9, ts_offset_days=4),
        _training_episode(scenario_id="b", rubric_score=0.9, ts_offset_days=2),
    ]
    assert _compute_graduation_status(eps) == "graduated"


async def test_training_episode_carries_scenario_id_and_rubric_score(
    tmp_path: Path,
):
    """End-to-end-shaped: workspace has a RUBRIC_SCORE signal, project
    is in training mode → episode captures both fields."""
    from dataclasses import dataclass
    from core.signals import Signal, SignalType

    @dataclass
    class StubProject:
        mode: str = "training"
        training_scenario_id: str | None = "scn_x"

    p, d = _persona(tmp_path)
    ws = Workspace(run_id="r_train", goal="x")
    ws.iteration = 1
    ws.board.append(Signal(
        type=SignalType.RUBRIC_SCORE,
        summary="rubric score: 0.85",
        from_agent="rubric_scorer",
        detail=json.dumps({"score": 0.85, "scenario_id": "scn_x"}),
    ))

    mw = MemoryWriter()
    await mw.on_run_complete(
        workspace=ws, project=StubProject(), run_id="r_train",
        run_root=tmp_path, personas={"developer": p},
    )

    eps = [
        json.loads(line)
        for line in (d / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(eps) == 1
    assert eps[0]["training"] is True
    assert eps[0]["scenario_id"] == "scn_x"
    assert eps[0]["rubric_score"] == 0.85
