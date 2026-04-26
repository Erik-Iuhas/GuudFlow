"""Shared fixtures for API tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from api.main import create_app
from api.storage import PersonaStore, ProjectStore, RunStore
from api.websocket import QuipRegistry, RunRegistry
from worker.queue import InMemoryJobQueue


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "personas").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def app(data_root: Path, monkeypatch):
    """Build a fresh app with state pointed at a temp data root."""
    monkeypatch.setenv("AGENTFLOW_DATA_DIR", str(data_root))
    monkeypatch.setenv("AGENTFLOW_API_TOKEN", "test-token")
    monkeypatch.delenv("AGENTFLOW_MODE", raising=False)

    a = create_app()
    # Manually wire state since lifespan only fires under uvicorn/TestClient.
    a.state.data_root = data_root
    a.state.project_store = ProjectStore(data_root)
    a.state.run_store = RunStore(data_root / "runs")
    a.state.persona_store = PersonaStore(data_root / "personas")
    a.state.queue = InMemoryJobQueue()
    a.state.run_registry = RunRegistry()
    a.state.quip_registry = QuipRegistry()
    return a


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def write_persona(data_root: Path):
    """Materialise a persona on disk."""
    def _write(persona_id: str, **profile_overrides) -> Path:
        d = data_root / "personas" / persona_id
        d.mkdir(parents=True, exist_ok=True)
        profile = {
            "persona_id": persona_id,
            "name": "Test",
            "specialty": "test",
            "character": "methodical",
            "stake_framing": "matters",
            **profile_overrides,
        }
        (d / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
        (d / "reputation.json").write_text(json.dumps({
            "production": {"total_runs": 0},
            "training": {"total_runs": 0, "graduation_status": "ungraduated"},
        }), encoding="utf-8")
        (d / "episodes.jsonl").write_text("", encoding="utf-8")
        return d
    return _write


@pytest.fixture
def write_run(data_root: Path):
    """Materialise a run's meta + board on disk."""
    def _write(run_id: str, project_id: str = "p1", board=None) -> Path:
        d = data_root / "runs" / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({
            "run_id": run_id,
            "project_id": project_id,
            "status": "complete",
            "created_at": "2026-04-25T00:00:00Z",
        }), encoding="utf-8")
        (d / "board.json").write_text(json.dumps(board or []), encoding="utf-8")
        (d / "workspace" / "deliverables").mkdir(parents=True, exist_ok=True)
        return d
    return _write
