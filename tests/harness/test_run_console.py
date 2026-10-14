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
