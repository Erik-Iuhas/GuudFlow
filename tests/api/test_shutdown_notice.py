"""A live run is told when the API process (and its LLM gateway) shuts down (F11)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from api.main import API_SHUTDOWN_NOTICE, _notify_live_runs_of_shutdown, app as main_app
from api.websocket import RunRegistry
from core.signals import SignalType
from core.workspace import Workspace


def test_notice_posts_failed_on_each_live_run(caplog: pytest.LogCaptureFixture) -> None:
    registry = RunRegistry()
    registry.runs["run_b"] = Workspace(run_id="run_b", goal="x")
    registry.runs["run_a"] = Workspace(run_id="run_a", goal="y")
    with caplog.at_level(logging.WARNING):
        assert _notify_live_runs_of_shutdown(registry) == ["run_a", "run_b"]
    for ws in registry.runs.values():
        last = ws.board[-1]
        assert last.type is SignalType.FAILED and last.from_agent == "runner"
        assert last.summary == API_SHUTDOWN_NOTICE
    assert "run_a, run_b" in caplog.text


def test_notice_is_silent_without_live_runs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert _notify_live_runs_of_shutdown(RunRegistry()) == []
    assert caplog.text == ""


async def test_lifespan_shutdown_notifies_live_runs(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUUDFLOW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GUUDFLOW_DISABLE_INPROC_WORKER", "1")
    monkeypatch.setenv("GUUDFLOW_DISABLE_AUTOPILOT", "1")
    async with main_app.router.lifespan_context(main_app):
        ws = Workspace(run_id="run_live", goal="x")
        main_app.state.run_registry.runs["run_live"] = ws
    assert ws.board[-1].summary == API_SHUTDOWN_NOTICE
