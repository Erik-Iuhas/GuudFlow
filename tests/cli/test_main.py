"""Argparse wiring + command dispatch — exercise every subcommand
with a stubbed Client so we don't need a live server."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

import cli.main as cli_main
from cli.client import APIError


@pytest.fixture(autouse=True)
def _disable_color(monkeypatch):
    monkeypatch.setenv("GUUDFLOW_CLI_NO_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.fixture
def stub_client(monkeypatch):
    """Replace cli.main.Client with a MagicMock so each command call
    is observable. Returns the mock instance."""
    instance = MagicMock()
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(cli_main, "Client", factory)
    return instance


def test_parser_help_runs_without_error(capsys):
    parser = cli_main.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "GuudFlow CLI" in out


def test_no_command_prints_help_and_exits_2(capsys):
    parser = cli_main.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([])
    # argparse 'required=True' returns 2 on missing command.
    assert exc.value.code == 2


def test_health_ok(stub_client, capsys):
    stub_client.health.return_value = {"ok": True}
    rc = cli_main.main(["health"])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_health_json(stub_client, capsys):
    stub_client.health.return_value = {"ok": True}
    rc = cli_main.main(["--json", "health"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True}


def test_dashboard_renders_table(stub_client, capsys):
    stub_client.dashboard.return_value = {
        "active_runs": [],
        "pending_approvals": [],
        "recent_completions": [],
        "top_personas": [],
    }
    rc = cli_main.main(["dashboard"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Active runs" in out


def test_projects_default_action_is_list(stub_client, capsys):
    stub_client.list_projects.return_value = [
        {"project_id": "p1", "goal": "ship it", "agents": [{"role": "dev"}]}
    ]
    rc = cli_main.main(["projects"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "p1" in out
    assert "ship it" in out


def test_projects_show(stub_client, capsys):
    stub_client.get_project.return_value = {
        "project_id": "demo",
        "goal": "g",
        "agents": [
            {"role": "dev", "persona_id": "alex", "model_tier": "solid", "depends_on": []}
        ],
    }
    rc = cli_main.main(["projects", "show", "demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "alex" in out


def test_projects_delete_requires_yes(stub_client, capsys):
    rc = cli_main.main(["projects", "delete", "demo"])
    assert rc == 2
    assert not stub_client.delete_project.called


def test_projects_delete_with_yes(stub_client, capsys):
    rc = cli_main.main(["projects", "delete", "demo", "--yes"])
    assert rc == 0
    stub_client.delete_project.assert_called_once_with("demo")


def test_runs_list(stub_client, capsys):
    stub_client.list_runs.return_value = [
        {"run_id": "r1", "project_id": "p", "status": "complete"},
    ]
    rc = cli_main.main(["runs", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "r1" in out


def test_runs_board_filters_by_type(stub_client, capsys):
    stub_client.get_run.return_value = {"run_id": "r1", "status": "complete"}
    stub_client.get_run_board.return_value = [
        {"id": "1", "type": "STARTED", "summary": "boot"},
        {"id": "2", "type": "COMPLETE", "summary": "done"},
    ]
    rc = cli_main.main(["runs", "board", "r1", "--type", "COMPLETE"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "done" in out
    assert "boot" not in out


def test_runs_comment(stub_client, capsys):
    stub_client.post_run_comment.return_value = {
        "ok": True,
        "type": "USER_COMMENT",
        "iteration": 2,
    }
    rc = cli_main.main(["runs", "comment", "r1", "looks good"])
    assert rc == 0
    stub_client.post_run_comment.assert_called_once_with(
        "r1", "looks good", priority="normal"
    )


def test_runs_comment_high_priority(stub_client):
    stub_client.post_run_comment.return_value = {
        "ok": True,
        "type": "USER_COMMENT_PRIORITY",
        "iteration": 2,
    }
    rc = cli_main.main(["runs", "comment", "r1", "stop", "--high"])
    assert rc == 0
    _, kwargs = stub_client.post_run_comment.call_args
    assert kwargs["priority"] == "high"


def test_runs_approve_grant(stub_client):
    stub_client.post_run_approval.return_value = {"decision": "granted"}
    rc = cli_main.main(["runs", "approve", "r1", "approval_x"])
    assert rc == 0
    stub_client.post_run_approval.assert_called_once_with(
        "r1", "approval_x", "grant", note=None
    )


def test_runs_approve_deny_with_note(stub_client):
    stub_client.post_run_approval.return_value = {"decision": "denied"}
    rc = cli_main.main(
        ["runs", "approve", "r1", "approval_x", "--deny", "--note", "too risky"]
    )
    assert rc == 0
    stub_client.post_run_approval.assert_called_once_with(
        "r1", "approval_x", "deny", note="too risky"
    )


def test_runs_stop(stub_client):
    stub_client.stop_run.return_value = {"ok": True}
    rc = cli_main.main(["runs", "stop", "r1"])
    assert rc == 0
    stub_client.stop_run.assert_called_once_with("r1")


def test_runs_resume(stub_client):
    stub_client.resume_run.return_value = {"job_id": "j1", "run_id": "r1"}
    rc = cli_main.main(["runs", "resume", "r1"])
    assert rc == 0
    stub_client.resume_run.assert_called_once_with("r1")


def test_personas_list(stub_client, capsys):
    stub_client.list_personas.return_value = [
        {"persona_id": "alex", "name": "Alex", "specialty": "dev", "n_episodes": 4}
    ]
    rc = cli_main.main(["personas"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alex" in out
    assert "Alex" in out


def test_personas_show(stub_client, capsys):
    stub_client.get_persona.return_value = {
        "persona_id": "alex",
        "name": "Alex",
        "specialty": "dev",
        "n_episodes": 2,
        "reputation": {"production": {"total_runs": 4, "approved_runs": 3}},
    }
    rc = cli_main.main(["personas", "show", "alex"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Alex" in out
    assert "75%" in out  # 3/4


def test_examples_list(stub_client, capsys):
    stub_client.list_examples.return_value = [
        {"id": "rougelike", "title": "Roguelike", "tags": ["game"], "description": "x"}
    ]
    rc = cli_main.main(["examples"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rougelike" in out


def test_examples_use_passes_overrides(stub_client):
    stub_client.instantiate_example.return_value = {
        "from_template": "rougelike",
        "project": {"project_id": "my-game"},
    }
    rc = cli_main.main(
        ["examples", "use", "rougelike", "--project-id", "my-game", "--goal", "fun"]
    )
    assert rc == 0
    stub_client.instantiate_example.assert_called_once_with(
        "rougelike", project_id="my-game", goal="fun"
    )


def test_propose_requires_input(stub_client, capsys):
    rc = cli_main.main(["propose"])
    assert rc == 2
    assert "requires --goal or --project" in capsys.readouterr().err


def test_propose_with_goal(stub_client, capsys):
    stub_client.propose.return_value = {
        "rationale": "baseline",
        "agents": [{"role": "dev", "depends_on": [], "description": "writes code"}],
    }
    rc = cli_main.main(["propose", "--goal", "ship it"])
    assert rc == 0
    stub_client.propose.assert_called_once_with(goal="ship it", project_id=None)
    out = capsys.readouterr().out
    assert "baseline" in out
    assert "writes code" in out


def test_propose_pulls_goal_from_project(stub_client, capsys):
    stub_client.get_project.return_value = {"goal": "ship from project"}
    stub_client.propose.return_value = {"rationale": "ok", "agents": []}
    rc = cli_main.main(["propose", "--project", "demo"])
    assert rc == 0
    stub_client.propose.assert_called_once_with(
        goal="ship from project", project_id="demo"
    )


def test_invoke_cost_preview_does_not_queue(stub_client, capsys):
    stub_client.invoke.return_value = {
        "accepted": False,
        "requires_approval": True,
        "estimated_cost": {"min_cost_usd": 0.10, "max_cost_usd": 0.50},
    }
    rc = cli_main.main(["invoke", "demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cost preview" in out
    assert "$0.5" in out or "0.50" in out


def test_invoke_with_confirm(stub_client, capsys):
    stub_client.invoke.return_value = {
        "accepted": True,
        "run_id": "run_1",
        "job_id": "job_1",
    }
    rc = cli_main.main(["invoke", "demo", "--confirm"])
    assert rc == 0
    _, kwargs = stub_client.invoke.call_args
    args, _ = stub_client.invoke.call_args
    assert args[0] == "demo"
    body = args[1]
    assert body == {"confirm": True}


def test_api_error_prints_to_stderr_returns_1(stub_client, capsys):
    stub_client.list_projects.side_effect = APIError(500, "boom", "/x")
    rc = cli_main.main(["projects"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "500" in err and "boom" in err


def test_api_error_401_emits_token_hint(stub_client, capsys):
    stub_client.list_projects.side_effect = APIError(401, "missing token", "/x")
    rc = cli_main.main(["projects"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "GUUDFLOW_API_TOKEN" in err
