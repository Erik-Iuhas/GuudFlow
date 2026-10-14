"""The run console: host-side log lines kept with the run, and the stop explanation."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from core.harness import run_console


def test_log_lines_inside_capture_reach_the_run_file(tmp_path: Path) -> None:
    log = logging.getLogger("core.harness.test_console")
    with run_console.capture(tmp_path):
        log.info("spawning bram")
        try:
            raise ValueError("no model key")
        except ValueError:
            log.exception("spawn failed")
    log.info("outside the run")
    out = run_console.read_console(tmp_path)
    texts = [r["text"] for r in out["lines"]]
    assert "spawning bram" in texts
    assert any("spawn failed" in t and "no model key" in t for t in texts)
    assert not any("outside the run" in t for t in texts)


def test_role_server_logs_are_returned(tmp_path: Path) -> None:
    d = tmp_path / "opencode_home" / "bram"
    d.mkdir(parents=True)
    (d / "opencode-serve.log").write_text("listening\nerror: bad key\n")
    assert run_console.read_console(tmp_path)["roles"] == [
        {"role": "bram", "lines": ["listening", "error: bad key"]}]


def test_a_run_that_never_spawned_says_so() -> None:
    agents = [SimpleNamespace(role="bram", depends_on=[]),
              SimpleNamespace(role="uma", depends_on=["bram"])]
    text = run_console.describe_end(agents, "stuck", {}, 0, False)
    assert "bram: spawned 0x, never started" in text and "uma" in text
    assert "no role was ever spawned" in text and "plain roles" in text


def test_the_end_report_names_the_planner_that_made_no_cards() -> None:
    agents = [SimpleNamespace(role="tech_lead", depends_on=[]),
              SimpleNamespace(role="developer", depends_on=["tech_lead"])]
    text = run_console.describe_end(agents, "stuck", {"tech_lead": 1}, 3, True,
                                    {"tech_lead": "FAILED: no plan submitted"}, 0)
    assert "cards created: 0" in text and "last result: FAILED: no plan submitted" in text
    assert "the planner ran but created no cards" in text


def test_no_plan_detector_fires_when_a_role_ran_and_the_board_is_empty() -> None:
    from core.harness.detectors import RunView, no_plan, DetectorConfig
    from core.signals import Signal, SignalType
    ran = Signal(type=SignalType.FAILED, from_agent="tech_lead", summary="no valid plan")
    view = RunView(signals=[ran], cards=[], role_states={}, running=[])
    (stall,) = no_plan(view, DetectorConfig())
    assert stall.detector == "no_plan" and "tech_lead: no valid plan" in stall.detail
    assert no_plan(RunView(signals=[], cards=[], role_states={}, running=[]), DetectorConfig()) == []
    assert no_plan(RunView(signals=[ran], cards=[], role_states={}, running=["tech_lead"]),
                   DetectorConfig()) == []
