"""Unit tests for core/oneshot.py and the CLI argument handling (no network, no model)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.oneshot import build_parser, main
from core.oneshot import (
    DEFAULT_TEAM_PATH,
    OneShotRequest,
    build_oneshot_project,
    load_team,
    oneshot_summary,
    project_to_dict,
    render_cost_table,
    seed_example_inputs,
    summarise_costs,
)
from core.scheduler import SchedulerStatus


def test_default_team_file_shape() -> None:
    team = load_team()
    roles = {a["role"]: a for a in team["agents"]}
    assert list(roles) == ["researcher", "planner", "builder_a", "builder_b", "reviewer"]
    assert roles["researcher"]["role_type"] == "researcher"
    assert roles["researcher"]["capabilities"] == {"web": "search+fetch"}
    assert roles["planner"]["role_type"] == "planner" and roles["planner"]["depends_on"] == ["researcher"]
    for b in ("builder_a", "builder_b"):
        assert roles[b]["role_type"] == "builder" and roles[b]["depends_on"] == ["planner"]
    rev = roles["reviewer"]
    assert rev["is_reviewer"] and rev["max_review_rounds"] == 2
    assert rev["depends_on"] == ["builder_a", "builder_b"] == rev["review_of"]
    assert all(a["model_tier"] == "solid" and a["description"] for a in roles.values())
    assert team["version"] == 1


def test_build_project_with_researcher() -> None:
    p = build_oneshot_project(OneShotRequest(goal="  Build X  ", budget_usd=7.5, demo=True,
                                             project_id="mine"))
    assert p.project_id == "mine" and p.goal == "Build X"
    assert p.harness == "opencode"
    assert [a.role for a in p.agents] == ["researcher", "planner", "builder_a", "builder_b", "reviewer"]
    assert p.approval_gates["token_budget_usd"] == 7.5
    assert p.approval_gates["demo"] is True
    assert p.approval_gates["pre_run_cost_preview"] is False
    assert next(a for a in p.agents if a.role == "reviewer").review_of == ["builder_a", "builder_b"]


def test_default_project_id_and_non_demo_keeps_preview() -> None:
    p = build_oneshot_project(OneShotRequest(goal="x"))
    assert p.project_id.startswith("oneshot-") and len(p.project_id) == len("oneshot-") + 8
    assert p.approval_gates["demo"] is False and p.approval_gates["pre_run_cost_preview"] is True


def test_web_off_drops_researcher() -> None:
    p = build_oneshot_project(OneShotRequest(goal="x", web="off"))
    roles = [a.role for a in p.agents]
    assert "researcher" not in roles and roles[0] == "planner"
    assert all("researcher" not in a.depends_on for a in p.agents)
    assert next(a for a in p.agents if a.role == "planner").depends_on == []
    # No other role ever gets web access.
    assert all(a.capabilities.get("web") in (None, "off") for a in p.agents)


def test_project_dict_has_no_secrets_and_roundtrips() -> None:
    from worker.job_runner import serialise_project
    p = build_oneshot_project(OneShotRequest(goal="x", budget_usd=3))
    data = project_to_dict(p)
    assert "api_key" not in json.dumps(data).lower()
    back = serialise_project(data)
    assert back.harness == "opencode" and back.approval_gates["token_budget_usd"] == 3
    assert [a.role for a in back.agents] == [a.role for a in p.agents]
    assert next(a for a in back.agents if a.role == "reviewer").review_of == ["builder_a", "builder_b"]


@pytest.mark.parametrize("kwargs", [
    {"goal": ""}, {"goal": "   "}, {"goal": "x", "budget_usd": 0}, {"goal": "x", "budget_usd": -1},
    {"goal": "x", "budget_usd": 1000.01}, {"goal": "x", "budget_usd": float("nan")},
    {"goal": "x", "web": "everyone"}, {"goal": "x", "project_id": " "},
])
def test_validation_rejects_bad_requests(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        build_oneshot_project(OneShotRequest(**kwargs))


def test_budget_bounds_are_inclusive_at_the_top() -> None:
    assert build_oneshot_project(OneShotRequest(goal="x", budget_usd=1000)).approval_gates[
        "token_budget_usd"] == 1000.0
    assert build_oneshot_project(OneShotRequest(goal="x", budget_usd=0.01))


def test_unknown_example_rejected_and_known_recorded() -> None:
    with pytest.raises(ValueError):
        build_oneshot_project(OneShotRequest(goal="x"), example="no-such-example")
    with pytest.raises(ValueError):
        build_oneshot_project(OneShotRequest(goal="x"), example="../data")
    p = build_oneshot_project(OneShotRequest(goal="x"), example="api-client-library")
    assert p.approval_gates["example"] == "api-client-library"


def test_seed_example_inputs_without_seed_dir_is_a_noop(tmp_path: Path) -> None:
    assert seed_example_inputs("api-client-library", tmp_path) == []


def test_custom_team_path(tmp_path: Path) -> None:
    team = {"team_id": "t", "agents": [{"role": "solo", "role_type": "builder"}]}
    path = tmp_path / "team.json"
    path.write_text(json.dumps(team))
    p = build_oneshot_project(OneShotRequest(goal="x"), team_path=path)
    assert [a.role for a in p.agents] == ["solo"]
    assert DEFAULT_TEAM_PATH.is_file()


# ── Costs ──────────────────────────────────────────────────────────────────


def _cost_log(run_root: Path) -> None:
    rows = [
        {"role": "planner", "input_tokens": 1000, "output_tokens": 200, "images": 0, "cost_usd": 0.01},
        {"role": "builder_a", "input_tokens": 3000, "output_tokens": 800, "images": 0, "cost_usd": 0.05},
        {"role": "builder_a", "input_tokens": 2000, "output_tokens": 500, "images": 2, "cost_usd": 0.03},
        {"role": "reviewer", "input_tokens": 500, "output_tokens": 50, "images": 0, "cost_usd": 0.004},
    ]
    lines = [json.dumps(r) for r in rows] + ["", "not json", json.dumps([1])]
    (run_root / "llm_costs.jsonl").write_text("\n".join(lines) + "\n")


def test_summarise_costs(tmp_path: Path) -> None:
    _cost_log(tmp_path)
    rows = summarise_costs(tmp_path)
    assert [r["role"] for r in rows] == ["planner", "builder_a", "reviewer", "TOTAL"]
    a = rows[1]
    assert a["calls"] == 2 and a["input_tokens"] == 5000 and a["output_tokens"] == 1300
    assert a["images"] == 2 and a["cost_usd"] == pytest.approx(0.08)
    total = rows[-1]
    assert total["calls"] == 4 and total["input_tokens"] == 6500
    assert total["cost_usd"] == pytest.approx(0.094)


def test_summarise_costs_missing_log(tmp_path: Path) -> None:
    assert summarise_costs(tmp_path) == []
    assert render_cost_table([]) == "No model calls recorded."


def test_render_cost_table_is_fixed_width(tmp_path: Path) -> None:
    _cost_log(tmp_path)
    text = render_cost_table(summarise_costs(tmp_path))
    lines = text.splitlines()
    assert lines[0].split()[0] == "role" and "cost (USD)" in lines[0]
    assert lines[-1].startswith("TOTAL") and lines[-1].endswith("$0.0940")
    assert len({len(line) for line in lines}) == 1  # every line the same width
    assert "$0.0800" in text and "6,500" in text


# ── Summary ────────────────────────────────────────────────────────────────


def test_oneshot_summary(tmp_path: Path) -> None:
    _cost_log(tmp_path)
    cards = [
        {"task_id": "root:planner", "column": "todo"},
        {"task_id": "T1", "title": "Addition", "assigned_to": "builder_a", "column": "in_progress"},
        {"task_id": "T1", "title": "Addition", "assigned_to": "builder_a", "column": "done"},
        {"task_id": "T2", "title": "Multiply", "assigned_to": "builder_b", "column": "needs_attention"},
        "garbage-line",
    ]
    (tmp_path / "tasks.jsonl").write_text(
        "\n".join(c if isinstance(c, str) else json.dumps(c) for c in cards) + "\n")
    result = SimpleNamespace(status=SchedulerStatus.COMPLETE, completed=["planner"], failed=["x"])
    s = oneshot_summary(tmp_path, result)
    assert s["status"] == "complete" and s["completed"] == ["planner"] and s["failed"] == ["x"]
    assert list(s["cards"]) == ["done", "needs_attention"]
    assert s["cards"]["done"] == [{"id": "T1", "title": "Addition", "owner": "builder_a"}]
    assert s["files"] == []  # no working repo
    assert s["costs"][-1]["role"] == "TOTAL"


def test_oneshot_summary_lists_merged_files(tmp_path: Path) -> None:
    import subprocess
    working = tmp_path / "workspace" / "working"
    working.mkdir(parents=True)
    run = lambda *a: subprocess.run(a, cwd=working, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q")
    (working / "b.py").write_text("x = 1\n")
    (working / "a.py").write_text("y = 2\n")
    run("git", "add", "-A")
    result = SimpleNamespace(status=SchedulerStatus.STUCK, completed=[], failed=[])
    s = oneshot_summary(tmp_path, result)
    assert s["status"] == "stuck" and s["files"] == ["a.py", "b.py"] and s["cards"] == {}


# ── CLI arguments ──────────────────────────────────────────────────────────


def test_cli_parser_defaults_and_flags() -> None:
    a = build_parser().parse_args(["Do it"])
    assert (a.goal, a.web, a.budget, a.demo, a.example, a.sandbox) == (
        "Do it", "researcher", 5.0, False, None, "auto")
    b = build_parser().parse_args(["G", "--web", "off", "--budget", "2.5", "--demo",
                                   "--example", "api-client-library", "--sandbox", "host"])
    assert (b.web, b.budget, b.demo, b.example, b.sandbox) == (
        "off", 2.5, True, "api-client-library", "host")


def test_cli_rejects_bad_input_with_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["x", "--budget", "0"], ["  "], ["x", "--example", "nope"], ["x", "--web", "bing"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    assert "budget" in capsys.readouterr().err


def test_cli_builds_the_right_project_and_settings(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    """main() hands the runner the opencode project and the chosen sandbox."""
    import cli.oneshot as cli

    seen: dict = {}

    class FakeRunner:
        def __init__(self, project, run_id, run_root, max_wall_seconds):
            seen.update(project=project, run_root=run_root, wall=max_wall_seconds)
            self.workspace = SimpleNamespace(subscribe=lambda cb: (lambda: None))
            self.harness_settings = None
            seen["runner"] = self

        async def run(self):
            return SimpleNamespace(status=SchedulerStatus.COMPLETE, completed=["planner"], failed=[])

    monkeypatch.setattr(cli, "AgentRunner", FakeRunner)
    rc = main(["Build it", "--web", "off", "--budget", "2", "--demo", "--sandbox", "host",
               "--run-dir", str(tmp_path / "r"), "--max-minutes", "3"])
    assert rc == 0
    project = seen["project"]
    assert project.harness == "opencode" and project.goal == "Build it"
    assert "researcher" not in [a.role for a in project.agents]
    assert project.approval_gates["token_budget_usd"] == 2.0 and project.approval_gates["demo"]
    assert seen["runner"].harness_settings.sandbox == "host"
    assert seen["wall"] == 180 and seen["run_root"] == tmp_path / "r"
