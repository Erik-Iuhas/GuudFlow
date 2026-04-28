"""Integration tests for M0 — persona persistence guarantees.

Covers:
1. _bootstrap_personas writes all five skeleton files.
2. Persona still appears in PersonaStore.list() after a mid-run crash
   (memory_writer never called), as a degraded entry.
3. After memory_writer completes, persona is non-degraded and profile.json
   has last_active_run_id / last_active_ts set.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.storage import PersonaStore
from agents.builtin.memory_writer import MemoryWriter
from agents.persona import Persona
from core.runner import AgentConfig, ProjectConfig
from core.workspace import Workspace
from worker.job_runner import _bootstrap_personas


REQUIRED_FILES = ("profile.json", "episodes.jsonl", "patterns.json", "reputation.json", "style.json")


def _make_project(persona_id: str) -> ProjectConfig:
    return ProjectConfig(
        project_id="proj-test",
        goal="test",
        agents=[AgentConfig(role="developer", persona_id=persona_id)],
    )


# ── 1. Bootstrap writes all skeleton files ────────────────────────────────


def test_bootstrap_writes_all_skeleton_files(tmp_path: Path):
    personas_root = tmp_path / "personas"
    personas_root.mkdir()
    project = _make_project("aria-dev-v1")

    _bootstrap_personas(project, personas_root)

    persona_dir = personas_root / "aria-dev-v1"
    assert persona_dir.is_dir()
    for filename in REQUIRED_FILES:
        assert (persona_dir / filename).exists(), f"missing {filename}"


def test_bootstrap_skips_existing_dir(tmp_path: Path):
    personas_root = tmp_path / "personas"
    personas_root.mkdir()
    persona_dir = personas_root / "aria-dev-v1"
    persona_dir.mkdir()
    # Write a sentinel profile so we can verify bootstrap didn't overwrite it.
    (persona_dir / "profile.json").write_text(
        json.dumps({"persona_id": "aria-dev-v1", "name": "Existing", "sentinel": True}),
        encoding="utf-8",
    )

    project = _make_project("aria-dev-v1")
    _bootstrap_personas(project, personas_root)

    data = json.loads((persona_dir / "profile.json").read_text(encoding="utf-8"))
    assert data.get("sentinel") is True  # not overwritten


def test_bootstrap_patterns_json_structure(tmp_path: Path):
    personas_root = tmp_path / "personas"
    personas_root.mkdir()
    _bootstrap_personas(_make_project("mike-backend-v2"), personas_root)
    patterns = json.loads((personas_root / "mike-backend-v2" / "patterns.json").read_text(encoding="utf-8"))
    assert patterns["patterns"] == []
    assert patterns["anti_patterns"] == []
    assert "version" in patterns


def test_bootstrap_reputation_json_structure(tmp_path: Path):
    personas_root = tmp_path / "personas"
    personas_root.mkdir()
    _bootstrap_personas(_make_project("sara-v1"), personas_root)
    rep = json.loads((personas_root / "sara-v1" / "reputation.json").read_text(encoding="utf-8"))
    assert rep["total_episodes"] == 0
    assert "production" in rep
    assert "training" in rep


# ── 2. Mid-run crash: persona appears as degraded in roster ──────────────


async def test_persona_with_missing_skeleton_files_marked_degraded(tmp_path: Path):
    """A persona dir with only profile.json (e.g. from manual deletion,
    external corruption, or pre-M0 bootstrap state) must still appear in
    the roster, marked degraded with missing files enumerated.
    """
    personas_root = tmp_path / "personas"
    persona_dir = personas_root / "partial-persona-v1"
    persona_dir.mkdir(parents=True)
    (persona_dir / "profile.json").write_text(
        json.dumps({"persona_id": "partial-persona-v1", "name": "Partial"}),
        encoding="utf-8",
    )
    # Deliberately DO NOT write episodes.jsonl / patterns.json etc.

    store = PersonaStore(personas_root)
    entries = await store.list()

    assert len(entries) == 1
    entry = entries[0]
    assert entry["persona_id"] == "partial-persona-v1"
    assert entry["degraded"] is True
    assert "episodes.jsonl" in entry["missing_files"]
    assert "patterns.json" in entry["missing_files"]


async def test_persona_with_no_profile_appears_degraded(tmp_path: Path):
    """A persona dir missing profile.json entirely must still appear in
    the roster (not silently filtered) — surfaces the bug instead of
    hiding it. Falls back to dir name as persona_id.
    """
    personas_root = tmp_path / "personas"
    persona_dir = personas_root / "orphan-v1"
    persona_dir.mkdir(parents=True)

    store = PersonaStore(personas_root)
    entries = await store.list()

    assert len(entries) == 1
    assert entries[0]["persona_id"] == "orphan-v1"
    assert entries[0]["degraded"] is True
    assert "profile.json" in entries[0]["missing_files"]


def test_bootstrap_raises_on_oserror(tmp_path: Path, monkeypatch):
    """The old behaviour silently swallowed OSError, leaving personas
    invisibly missing. The new behaviour must log and re-raise so the
    failure surfaces.
    """
    personas_root = tmp_path / "personas"
    personas_root.mkdir()

    real_mkdir = Path.mkdir

    def fail_mkdir(self, *args, **kwargs):
        if self.parent == personas_root:
            raise OSError("simulated disk failure")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    with pytest.raises(OSError, match="simulated disk failure"):
        _bootstrap_personas(_make_project("doomed-v1"), personas_root)


async def test_fully_bootstrapped_persona_not_degraded(tmp_path: Path):
    personas_root = tmp_path / "personas"
    personas_root.mkdir()
    _bootstrap_personas(_make_project("healthy-v1"), personas_root)

    store = PersonaStore(personas_root)
    entries = await store.list()

    assert len(entries) == 1
    assert entries[0]["degraded"] is False
    assert entries[0]["missing_files"] == []


# ── 3. After memory_writer: profile.json has last_active fields ──────────


async def test_memory_writer_updates_profile_last_active(tmp_path: Path):
    personas_root = tmp_path / "personas"
    personas_root.mkdir()
    persona_id = "aria-dev-v1"
    _bootstrap_personas(_make_project(persona_id), personas_root)

    persona = Persona.load(persona_id, personas_root)
    persona._dir = personas_root / persona_id

    ws = Workspace(run_id="run-xyz", goal="Add auth")
    ws.iteration = 1

    mw = MemoryWriter()
    await mw.on_run_complete(
        workspace=ws,
        project=_make_project(persona_id),
        run_id="run-xyz",
        run_root=tmp_path,
        personas={"developer": persona},
    )

    profile = json.loads((personas_root / persona_id / "profile.json").read_text(encoding="utf-8"))
    assert profile.get("last_active_run_id") == "run-xyz"
    assert profile.get("last_active_ts") is not None

    store = PersonaStore(personas_root)
    entries = await store.list()
    assert len(entries) == 1
    assert entries[0]["degraded"] is False


async def test_memory_writer_save_creates_dir_if_missing(tmp_path: Path):
    """If persona dir somehow disappeared before _save(), it is recreated."""
    persona_id = "ghost-v1"
    persona_dir = tmp_path / "personas" / persona_id
    persona_dir.mkdir(parents=True)
    (persona_dir / "profile.json").write_text(
        json.dumps({"persona_id": persona_id, "name": "Ghost"}), encoding="utf-8"
    )
    persona = Persona.load(persona_id, tmp_path / "personas")
    persona._dir = persona_dir

    # Simulate dir disappearing mid-run.
    import shutil
    shutil.rmtree(persona_dir)

    mw = MemoryWriter()
    ws = Workspace(run_id="run-ghost", goal="test")
    await mw.on_run_complete(
        workspace=ws,
        project=ProjectConfig(project_id="p", goal="test", agents=[]),
        run_id="run-ghost",
        run_root=tmp_path,
        personas={"developer": persona},
    )

    # Directory recreated and files present.
    assert persona_dir.exists()
    assert (persona_dir / "episodes.jsonl").exists()
    assert (persona_dir / "profile.json").exists()
    profile = json.loads((persona_dir / "profile.json").read_text(encoding="utf-8"))
    assert profile["last_active_run_id"] == "run-ghost"
