"""GUUDFLOW_SANDBOX picks where agents run by default (docs/SETUP.md)."""

from __future__ import annotations

import pytest

from cli.oneshot import build_parser
from core.harness.run import HarnessSettings, sandbox_from_env


@pytest.mark.parametrize("value, expected", [
    (None, "auto"), ("", "auto"), ("host", "host"), ("DOCKER", "docker"), (" auto ", "auto"),
    ("container", "auto"),   # not a mode: warned about, then auto
])
def test_sandbox_mode_comes_from_the_environment(monkeypatch, value, expected) -> None:
    if value is None:
        monkeypatch.delenv("GUUDFLOW_SANDBOX", raising=False)
    else:
        monkeypatch.setenv("GUUDFLOW_SANDBOX", value)
    assert sandbox_from_env() == expected
    assert HarnessSettings().sandbox == expected       # API and Studio runs use the default


def test_cli_flag_wins_and_defaults_to_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("GUUDFLOW_SANDBOX", "host")
    assert build_parser().parse_args(["goal"]).sandbox is None        # no flag: HarnessSettings() reads the env
    assert build_parser().parse_args(["goal", "--sandbox", "docker"]).sandbox == "docker"
