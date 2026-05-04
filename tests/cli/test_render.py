"""Render helpers — verify table layout, status colouring, and the
domain renderers don't blow up on partial payloads."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from cli import render


@pytest.fixture(autouse=True)
def _disable_color(monkeypatch):
    """Force colour off so the assertions can match plain strings."""
    monkeypatch.setenv("GUUDFLOW_CLI_NO_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")


def test_render_table_pads_to_widest_cell():
    out = render.render_table(["a", "b"], [["x", "yyyy"], ["zzzz", "w"]])
    lines = out.splitlines()
    # Header + separator + 2 data rows
    assert len(lines) == 4
    # Every line in the body is the same length (padded).
    body = lines[2:]
    assert len({len(line) for line in body}) == 1


def test_render_table_truncates_long_cells():
    out = render.render_table(
        ["x"], [["abcdefghijk"]], max_widths=[5]
    )
    last_line = out.splitlines()[-1]
    assert "…" in last_line
    assert "abcdefghijk" not in last_line


def test_fmt_relative_time_seconds_minutes():
    now = datetime.now(tz=timezone.utc)
    assert render.fmt_relative_time((now - timedelta(seconds=5)).isoformat()) == "5s"
    assert render.fmt_relative_time((now - timedelta(minutes=3)).isoformat()) == "3m"
    assert render.fmt_relative_time((now - timedelta(hours=2)).isoformat()) == "2h"
    assert render.fmt_relative_time((now - timedelta(days=4)).isoformat()) == "4d"


def test_fmt_relative_time_handles_garbage():
    assert render.fmt_relative_time(None) == "-"
    assert render.fmt_relative_time("") == "-"
    assert render.fmt_relative_time("not-a-date") == "not-a-date"


def test_fmt_status_unknown_status_renders_plain():
    # No ANSI when colour is disabled — exact match is fine.
    assert "weird" in render.fmt_status("weird")


def test_render_signal_row_handles_minimal_payload():
    sig = {"type": "STARTED", "summary": "boot"}
    line = render.render_signal_row(sig)
    assert "STARTED" in line
    assert "boot" in line


def test_render_signal_row_includes_actor_and_iteration():
    sig = {
        "type": "COMPLETE",
        "summary": "done",
        "from_agent": "developer",
        "iteration": 3,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }
    line = render.render_signal_row(sig)
    assert "developer" in line
    assert "i3" in line
    assert "COMPLETE" in line


def test_render_dashboard_empty_payload_does_not_raise():
    out = render.render_dashboard({})
    # Each section is announced even when empty.
    assert "Active runs" in out
    assert "Pending approvals" in out
    assert "Recent completions" in out
    assert "Top personas" in out


def test_render_dashboard_with_data():
    payload = {
        "active_runs": [
            {
                "run_id": "run_1",
                "project_id": "proj",
                "status": "running",
                "elapsed_s": 12,
                "task_progress": {"closed": 1, "total": 3},
                "current_agents": [{"role": "dev"}, {"role": "rev"}],
            }
        ],
        "pending_approvals": [
            {"run_id": "run_1", "approval_id": "a1", "kind": "cost", "title": "Confirm $0.50"}
        ],
        "recent_completions": [
            {
                "run_id": "run_0",
                "project_id": "proj",
                "outcome": "complete",
                "duration_s": 60,
                "ended_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        ],
        "top_personas": [
            {
                "persona_id": "alex",
                "name": "Alex",
                "role": "dev",
                "recent_runs": 4,
                "win_rate": 0.75,
            }
        ],
    }
    out = render.render_dashboard(payload)
    assert "run_1" in out
    assert "Confirm $0.50" in out
    assert "75%" in out


def test_render_run_summary_prefers_short_lines():
    meta = {
        "run_id": "run_x",
        "project_id": "proj_a",
        "status": "complete",
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_s": 42,
        "goal": "Do the thing",
    }
    out = render.render_run_summary(meta)
    assert "run_x" in out
    assert "proj_a" in out
    assert "Do the thing" in out
    assert "42s" in out
