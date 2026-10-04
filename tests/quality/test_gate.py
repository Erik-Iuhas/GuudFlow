"""Tests for the offline quality gate (S14): recorded run dirs are built in tmp_path.

Every generic check has a pass and a fail case; every example project has a pass
case and at least one fail case for its functional checks; plus the CLI exit code.
No network and no model is involved.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from tests.quality import conftest as quality_conftest
from tests.quality import functional
from tests.quality.__main__ import main as cli_main
from tests.quality.gate import CheckResult, GateReport, run_gate

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ["algorithm-with-benchmarks", "api-client-library", "bug-hunt-and-fix",
            "cli-tool-from-spec", "codebase-wiki", "fullstack-web-app"]

# ── Builders ───────────────────────────────────────────────────────────────


def sig(type_: str, role: str, summary: str = "", detail: str | None = None) -> dict[str, Any]:
    """One board signal."""
    return {"id": f"{type_}-{role}-{summary}", "type": type_, "summary": summary or f"{role} {type_.lower()}",
            "detail": detail, "from_agent": role, "from_user": False, "priority": "normal",
            "iteration": 0, "seen_by": [], "timestamp": "2026-10-04T19:00:00+00:00"}


def good_board() -> list[dict[str, Any]]:
    """A one-role run that completed cleanly."""
    result = json.dumps({"result": {"status": "done"}, "cost": 0.01, "tokens": {"input": 10, "output": 5}})
    return [sig("STARTED", "builder"), sig("CAPABILITIES", "builder"),
            sig("AGENT_RESULT", "builder", detail=result), sig("COMPLETE", "builder")]


def good_tasks() -> list[dict[str, Any]]:
    return [{"task_id": "T1", "column": "todo"}, {"task_id": "T1", "column": "in_progress"},
            {"task_id": "T1", "column": "done"}]


def good_costs() -> list[dict[str, Any]]:
    return [{"role": "builder", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}]


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True)


def make_run(tmp_path: Path, files: dict[str, str] | None = None, *,
             commits: list[dict[str, str | None]] | None = None,
             board: list[dict[str, Any]] | None = None,
             tasks: list[dict[str, Any]] | None = None,
             costs: list[dict[str, Any]] | None = None,
             symlinks: dict[str, str] | None = None) -> Path:
    """Build a recorded run dir: jsonl logs plus a merged repo on `main`.

    `commits` is a list of `{path: content | None}` (None deletes); the first is the
    "chore: initialise run workspace" commit. `files` is a shortcut for one fix-up
    commit after an empty initial commit.
    """
    run = tmp_path / "run"
    working = run / "workspace" / "working"
    working.mkdir(parents=True)
    _jsonl(run / "board.jsonl", good_board() if board is None else board)
    _jsonl(run / "tasks.jsonl", good_tasks() if tasks is None else tasks)
    _jsonl(run / "llm_costs.jsonl", good_costs() if costs is None else costs)
    if commits is None:
        commits = [{}, dict(files or {"a.py": "x = 1\n"})]
    _git(working, "init", "-q", "-b", "main")
    for i, changes in enumerate(commits):
        for rel, content in changes.items():
            path = working / rel
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        if i == len(commits) - 1:
            for rel, target in (symlinks or {}).items():
                (working / rel).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, working / rel)
        _git(working, "add", "-A")
        _git(working, "commit", "-q", "--allow-empty",
             "-m", "chore: initialise run workspace" if i == 0 else f"feat: step {i}")
    return run


def statuses(report: GateReport) -> dict[str, str]:
    """`{check name: PASS|FAIL|SKIP}`."""
    return {c.name: c.status for c in report.checks}


def check(report: GateReport, name: str) -> CheckResult:
    """The check called `name`."""
    for c in report.checks:
        if c.name == name:
            return c
    raise AssertionError(f"no check {name!r} in {[c.name for c in report.checks]}")


# ── Generic checks: the baseline and one fail case per check ───────────────


def test_clean_run_passes_every_generic_check(tmp_path: Path) -> None:
    report = run_gate(make_run(tmp_path))
    assert report.passed, report.render()
    assert len(report.checks) == 10
    assert all(c.status == "PASS" for c in report.checks)
    assert "PASSED: 10 passed" in report.render()


def test_missing_run_dir_fails(tmp_path: Path) -> None:
    report = run_gate(tmp_path / "nope")
    assert not report.passed
    assert check(report, "run directory").status == "FAIL"


@pytest.mark.parametrize("name,mutate", [
    ("no COMPLETE", lambda b: [s for s in b if s["type"] != "COMPLETE"]),
    ("role ended failed", lambda b: [*b, sig("FAILED", "builder", "builder reported failure")]),
    ("human never answered", lambda b: [*b, sig("HUMAN_NEEDED", "overseer", "which one?")]),
    ("empty board", lambda b: []),
])
def test_run_complete_fails(tmp_path: Path, name: str, mutate: Any) -> None:
    report = run_gate(make_run(tmp_path, board=mutate(good_board())))
    assert check(report, "run reached COMPLETE").status == "FAIL", name
    assert not report.passed


def test_run_complete_accepts_answered_human_question(tmp_path: Path) -> None:
    board = [*good_board()[:3], sig("HUMAN_NEEDED", "overseer"), sig("HUMAN_RESPONDED", "user"),
             good_board()[3]]
    assert check(run_gate(make_run(tmp_path, board=board)), "run reached COMPLETE").passed


def test_cards_done_fail_and_latest_line_wins(tmp_path: Path) -> None:
    stuck = [*good_tasks(), {"task_id": "T2", "column": "blocked"}, {"task_id": "root:plan", "column": "todo"}]
    res = check(run_gate(make_run(tmp_path, tasks=stuck)), "every card done")
    assert res.status == "FAIL" and "T2 in blocked" in res.detail and "root:plan" not in res.detail
    # T1 went todo -> in_progress -> done: the latest line wins.
    assert check(run_gate(make_run(tmp_path / "ok")), "every card done").passed


def test_cards_done_without_a_task_board_passes(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    (run / "tasks.jsonl").unlink()
    assert check(run_gate(run), "every card done").passed


def test_unresolved_failed_signal_fails_but_a_later_complete_resolves_it(tmp_path: Path) -> None:
    board = good_board()
    failing = [board[0], board[1], sig("FAILED", "builder", "no valid result"), *board[2:]]
    assert check(run_gate(make_run(tmp_path / "a", board=failing)), "no unresolved FAILED").passed
    stuck = [*board, sig("STARTED", "tester"), sig("CAPABILITIES", "tester"), sig("FAILED", "tester", "boom")]
    res = check(run_gate(make_run(tmp_path / "b", board=stuck)), "no unresolved FAILED")
    assert res.status == "FAIL" and "tester" in res.detail


def test_capabilities_missing_for_a_spawned_role_fails(tmp_path: Path) -> None:
    board = [s for s in good_board() if s["type"] != "CAPABILITIES"]
    res = check(run_gate(make_run(tmp_path, board=board)), "CAPABILITIES per role")
    assert res.status == "FAIL" and "builder" in res.detail


@pytest.mark.parametrize("detail", ["not json at all", json.dumps(["list"]), json.dumps({"no": "result"})])
def test_invalid_agent_result_fails(tmp_path: Path, detail: str) -> None:
    board = [sig("STARTED", "builder"), sig("CAPABILITIES", "builder"),
             sig("AGENT_RESULT", "builder", detail=detail), sig("COMPLETE", "builder")]
    assert check(run_gate(make_run(tmp_path, board=board)), "AGENT_RESULT validated").status == "FAIL"


def test_cost_log_missing_or_zero_fails(tmp_path: Path) -> None:
    run = make_run(tmp_path / "missing")
    (run / "llm_costs.jsonl").unlink()
    assert check(run_gate(run), "cost log").status == "FAIL"
    zero = [{"role": "builder", "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}]
    assert check(run_gate(make_run(tmp_path / "zero", costs=zero)), "cost log").status == "FAIL"


def test_cost_log_with_tokens_but_zero_dollars_passes(tmp_path: Path) -> None:
    """The recorded fake-model run costs $0.00 but has tokens: that is still spend."""
    free = [{"role": "builder", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0}]
    assert check(run_gate(make_run(tmp_path, costs=free)), "cost log").passed


def test_cost_log_not_required_without_model_calls(tmp_path: Path) -> None:
    run = make_run(tmp_path, board=[s for s in good_board() if s["type"] != "AGENT_RESULT"], costs=[])
    (run / "llm_costs.jsonl").unlink()
    assert check(run_gate(run), "cost log").passed


def test_secret_in_merged_tree_fails(tmp_path: Path) -> None:
    key = "AKIA" + "ABCDEFGHIJKLMNOP"
    res = check(run_gate(make_run(tmp_path, {"cfg.py": f'KEY = "{key}"\n'})), "no secrets in merged tree")
    assert res.status == "FAIL" and "cfg.py" in res.detail
    res = check(run_gate(make_run(tmp_path / "env", {".env": "A=1\n"})), "no secrets in merged tree")
    assert res.status == "FAIL"


@pytest.mark.parametrize("path", ["inputs/spec.md", ".github/workflows/ci.yml", "opencode.json", ".opencode/agent.md"])
def test_protected_path_in_merged_tree_fails(tmp_path: Path, path: str) -> None:
    res = check(run_gate(make_run(tmp_path, {path: "x\n", "a.py": "x=1\n"})), "no protected paths")
    assert res.status == "FAIL" and path in res.detail


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside", "../../x"])
def test_escaping_symlink_fails_and_inner_symlink_passes(tmp_path: Path, target: str) -> None:
    run = make_run(tmp_path / "bad", symlinks={"pkg/link": target})
    assert check(run_gate(run), "no escaping symlinks").status == "FAIL"
    run = make_run(tmp_path / "good", symlinks={"pkg/link": "../a.py"})
    assert check(run_gate(run), "no escaping symlinks").passed


def test_missing_repo_fails_every_tree_check(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    import shutil
    shutil.rmtree(run / "workspace" / "working" / ".git")
    report = run_gate(run)
    for name in ("merged repo present", "no secrets in merged tree", "no protected paths", "no escaping symlinks"):
        assert check(report, name).status == "FAIL", name


def test_the_recorded_workspace_is_not_modified(tmp_path: Path) -> None:
    files = {"pyproject.toml": "[project]\nname='p'\nversion='0'\n", "src/p/__init__.py": "",
             "tests/test_a.py": "def test_a():\n    assert True\n"}
    run = make_run(tmp_path, files)
    working = run / "workspace" / "working"
    before = sorted(p.relative_to(working).as_posix() for p in working.rglob("*") if ".git/" not in p.as_posix())
    run_gate(run, "cli-tool-from-spec")
    after = sorted(p.relative_to(working).as_posix() for p in working.rglob("*") if ".git/" not in p.as_posix())
    assert before == after
    assert not list(working.rglob("__pycache__")) and not list(working.rglob(".pytest_cache"))


def test_plain_pytest_never_collects_merged_trees(tmp_path: Path) -> None:
    inside = Path("x") / "run" / "workspace" / "working" / "tests" / "test_a.py"
    assert quality_conftest.pytest_ignore_collect(inside) is True
    assert quality_conftest.pytest_ignore_collect(Path("x") / "tests" / "quality" / "test_gate.py") is None
    # And for real: a merged tree under a collected directory is skipped by `pytest --collect-only`.
    work = tmp_path / "scan" / "run" / "workspace" / "working" / "tests"
    work.mkdir(parents=True)
    (work / "test_inner.py").write_text("def test_inner():\n    assert True\n")
    (tmp_path / "scan" / "conftest.py").write_text(
        (REPO_ROOT / "tests" / "quality" / "conftest.py").read_text())
    (tmp_path / "scan" / "test_outer.py").write_text("def test_outer():\n    assert True\n")
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          "--rootdir", str(tmp_path / "scan"), str(tmp_path / "scan")],
                         capture_output=True, text=True, cwd=tmp_path / "scan").stdout
    assert "test_outer" in out and "test_inner" not in out


# ── Offline enforcement ────────────────────────────────────────────────────


def test_functional_pytest_runs_offline_but_allows_loopback(tmp_path: Path) -> None:
    online = textwrap.dedent('''
        import socket
        def test_no_internet():
            socket.create_connection(("example.com", 80), timeout=2)
        ''')
    loop = textwrap.dedent('''
        import socket, threading
        def test_loopback_ok():
            srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
            threading.Thread(target=lambda: srv.accept(), daemon=True).start()
            socket.create_connection(srv.getsockname(), timeout=2).close()
        def test_env():
            import os
            assert os.environ["NO_NETWORK"] == "1"
            assert not any(k.lower().endswith("_proxy") for k in os.environ)
        ''')
    base = {"pyproject.toml": "[project]\nname='p'\nversion='0'\n", "src/p/__init__.py": ""}
    bad = run_gate(make_run(tmp_path / "bad", {**base, "tests/test_net.py": online}), "api-client-library")
    assert check(bad, "mocked tests pass offline").status == "FAIL"
    assert "network disabled" in check(bad, "mocked tests pass offline").detail
    good = run_gate(make_run(tmp_path / "good", {**base, "tests/test_loop.py": loop}), "api-client-library")
    assert check(good, "mocked tests pass offline").passed, check(good, "mocked tests pass offline").detail


def test_missing_declared_dependency_skips_but_undeclared_fails(tmp_path: Path) -> None:
    test = "import guudflow_absent_dep\n\ndef test_x():\n    assert True\n"
    declared = {"pyproject.toml": "[project]\nname='p'\nversion='0'\ndependencies=['guudflow-absent-dep']\n",
                "src/p/__init__.py": "", "tests/test_x.py": test}
    res = check(run_gate(make_run(tmp_path / "d", declared), "api-client-library"), "mocked tests pass offline")
    assert res.status == "SKIP" and "guudflow_absent_dep" in res.detail
    undeclared = {**declared, "pyproject.toml": "[project]\nname='p'\nversion='0'\n"}
    res = check(run_gate(make_run(tmp_path / "u", undeclared), "api-client-library"), "mocked tests pass offline")
    assert res.status == "FAIL"


def test_subprocess_timeout_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUUDFLOW_GATE_TIMEOUT", "2")
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    files = {"pyproject.toml": "[project]\nname='p'\nversion='0'\n", "src/p/__init__.py": "",
             "tests/test_slow.py": slow}
    res = check(run_gate(make_run(tmp_path, files), "api-client-library"), "mocked tests pass offline")
    assert res.status == "FAIL" and "timed out" in res.detail


# ── Functional checks: api-client-library ──────────────────────────────────

API_OK = {
    "pyproject.toml": "[project]\nname = 'demo-client'\nversion = '0.1'\ndependencies = []\n",
    "src/demo_client/__init__.py": "from demo_client.client import DemoClient\n",
    "src/demo_client/client.py": ("class DemoClient:\n    def fact(self) -> str:\n        return 'cat'\n"
                                  "    def _private(self):\n        return 1\n"),
    "tests/test_client.py": ("from demo_client import DemoClient\n\n"
                             "def test_fact():\n    assert DemoClient().fact() == 'cat'\n"),
    "README.md": "# demo\n\n## Install\n\npip install .\n\n## Usage\n\n```python\nDemoClient().fact()\n```\n",
}


def test_api_client_library_passes(tmp_path: Path) -> None:
    report = run_gate(make_run(tmp_path, API_OK), "api-client-library")
    assert report.passed, report.render()
    assert {c.name for c in report.checks[10:]} == {
        "pyproject.toml", "package imports", "typed client class", "mocked tests pass offline",
        "README install + usage"}


def test_api_client_library_failures(tmp_path: Path) -> None:
    broken = {**API_OK,
              "src/demo_client/client.py": "class DemoClient:\n    def fact(self):\n        return 'cat'\n",
              "tests/test_client.py": "def test_fact():\n    assert False\n",
              "README.md": "# demo\n"}
    del broken["pyproject.toml"]
    s = statuses(run_gate(make_run(tmp_path, broken), "api-client-library"))
    assert s["pyproject.toml"] == "FAIL"
    assert s["typed client class"] == "FAIL"
    assert s["mocked tests pass offline"] == "FAIL"
    assert s["README install + usage"] == "FAIL"
    assert s["package imports"] == "PASS"
    # An import that cannot succeed.
    bad_import = {**API_OK, "src/demo_client/__init__.py": "from demo_client.missing import X\n"}
    assert statuses(run_gate(make_run(tmp_path / "i", bad_import), "api-client-library"))["package imports"] == "FAIL"


def test_project_id_forms_and_unknown_project(tmp_path: Path) -> None:
    run = make_run(tmp_path, API_OK)
    assert run_gate(run, "example-API-Client-Library").project == "api-client-library"
    report = run_gate(run, "nope")
    assert check(report, "functional checks").status == "FAIL" and not report.passed


# ── cli-tool-from-spec ─────────────────────────────────────────────────────

CLI_OK = {
    "pyproject.toml": "[project]\nname='democli'\nversion='0.1'\n[project.scripts]\ndemo = 'democli.cli:main'\n",
    "src/democli/__init__.py": "",
    "src/democli/cli.py": textwrap.dedent('''
        import argparse

        def main(argv=None):
            p = argparse.ArgumentParser(prog="demo")
            p.add_argument("--upper", action="store_true")
            a = p.parse_args(argv)
            print("HI" if a.upper else "hi")
            return 0
        '''),
    "src/democli/__main__.py": "import sys\nfrom democli.cli import main\nsys.exit(main())\n",
    "tests/test_cli.py": ("from democli.cli import main\n\ndef test_main(capsys):\n"
                          "    assert main([]) == 0\n    assert 'hi' in capsys.readouterr().out\n"),
    "README.md": "# demo\n\n## Install\n\n```\npip install -e .\n```\n\n## Usage\n\n```\ndemo --upper\n```\n",
    "docs/cli_spec.md": "# Spec\n" + "The demo command prints a greeting. " * 5,
}


def test_cli_tool_from_spec_passes(tmp_path: Path) -> None:
    report = run_gate(make_run(tmp_path, CLI_OK), "cli-tool-from-spec")
    assert report.passed, report.render()
    assert check(report, "--help works").detail == "demo --help"


def test_cli_tool_from_spec_failures(tmp_path: Path) -> None:
    invented = {**CLI_OK, "README.md": CLI_OK["README.md"] + "\nAlso `demo --bogus-flag`.\n"}
    res = check(run_gate(make_run(tmp_path / "a", invented), "cli-tool-from-spec"), "README flags exist in code")
    assert res.status == "FAIL" and "--bogus-flag" in res.detail
    no_script = {**CLI_OK, "pyproject.toml": "[project]\nname='democli'\nversion='0.1'\n"}
    s = statuses(run_gate(make_run(tmp_path / "b", no_script), "cli-tool-from-spec"))
    assert s["console script declared"] == "FAIL" and s["--help works"] == "PASS"  # falls back to __main__
    nothing = {k: v for k, v in no_script.items() if not k.endswith("__main__.py")}
    s = statuses(run_gate(make_run(tmp_path / "c", nothing), "cli-tool-from-spec"))
    assert s["--help works"] == "FAIL"
    crash = {**CLI_OK, "src/democli/cli.py": "def main(argv=None):\n    raise SystemExit(3)\n"}
    s = statuses(run_gate(make_run(tmp_path / "d", crash), "cli-tool-from-spec"))
    assert s["--help works"] == "FAIL" and s["tests pass"] == "FAIL"


# ── algorithm-with-benchmarks ──────────────────────────────────────────────

ALGO_OK = {
    "src/algo.py": ('def double_all(xs: list[int]) -> list[int]:\n    """Double each element: O(n) time and space."""\n'
                    "    return [x * 2 for x in xs]\n"),
    "tests/test_algo.py": textwrap.dedent('''
        from algo import double_all

        def test_empty():
            assert double_all([]) == []

        def test_single():
            assert double_all([2]) == [4]

        def test_many():
            assert double_all([1, 2, 3]) == [2, 4, 6]
        '''),
    "bench/benchmark.py": textwrap.dedent('''
        import time
        from algo import double_all
        print("| n | ours (s) | baseline (s) |")
        print("|---|---|---|")
        for n in (10, 100, 1000):
            t = time.perf_counter(); double_all(list(range(n))); d = time.perf_counter() - t
            print(f"| {n} | {d:.6f} | {d:.6f} |")
        '''),
    "RESULTS.md": "# Results\n\n| n | ours | baseline |\n|---|---|---|\n| 10 | 0.0001 | 0.0001 |\n\nLinear.\n",
}


def test_algorithm_with_benchmarks_passes(tmp_path: Path) -> None:
    report = run_gate(make_run(tmp_path, ALGO_OK), "algorithm-with-benchmarks")
    assert report.passed, report.render()
    assert not [c for c in report.checks if c.skipped]


def test_algorithm_with_benchmarks_failures(tmp_path: Path) -> None:
    broken = {**ALGO_OK, "bench/benchmark.py": "import sys\nsys.exit(1)\n",
              "RESULTS.md": "no table, just prose\n",
              "src/algo.py": "def double_all(xs):\n    return xs\n",
              "tests/test_algo.py": "def test_one():\n    assert True\n"}
    s = statuses(run_gate(make_run(tmp_path, broken), "algorithm-with-benchmarks"))
    assert s["benchmark runs"] == "FAIL"
    assert s["RESULTS.md table"] == "FAIL"
    assert s["entry point typed + documented"] == "FAIL"
    assert s["tests pass"] == "FAIL"  # passes but has a single test
    missing = {k: v for k, v in ALGO_OK.items() if not k.startswith("bench/")}
    assert statuses(run_gate(make_run(tmp_path / "m", missing), "algorithm-with-benchmarks"))["benchmark runs"] == "FAIL"


def test_benchmark_that_hangs_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUUDFLOW_GATE_TIMEOUT", "2")
    hang = {**ALGO_OK, "bench/benchmark.py": "import time\ntime.sleep(30)\n"}
    res = check(run_gate(make_run(tmp_path, hang), "algorithm-with-benchmarks"), "benchmark runs")
    assert res.status == "FAIL" and "did not finish" in res.detail


# ── bug-hunt-and-fix ───────────────────────────────────────────────────────

BUGGY = {"src/calc.py": "def add(a, b):\n    return a - b\n", "tests/__init__.py": "",
         "tests/test_old.py": "from src.calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n"}
FIX = {"src/calc.py": "def add(a, b):\n    return a + b\n",
       "tests/test_regression.py": "from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
       "docs/adr/0001-add-bug.md": "# ADR 1\n\nRoot cause: add() subtracted its operands. " * 5}


def test_bug_hunt_and_fix_passes(tmp_path: Path) -> None:
    report = run_gate(make_run(tmp_path, commits=[BUGGY, FIX]), "bug-hunt-and-fix")
    assert report.passed, report.render()
    assert not [c for c in report.checks if c.skipped]
    assert "fail on the pre-fix code" in check(report, "regression test fails without the fix").detail


def test_bug_hunt_regression_test_that_also_passes_before_the_fix_fails(tmp_path: Path) -> None:
    weak = {**FIX, "tests/test_regression.py": "def test_weak():\n    assert True\n"}
    s = statuses(run_gate(make_run(tmp_path, commits=[BUGGY, weak]), "bug-hunt-and-fix"))
    assert s["regression test fails without the fix"] == "FAIL"
    assert s["tests pass after fix"] == "PASS"


def test_bug_hunt_missing_pieces_fail(tmp_path: Path) -> None:
    no_adr = {k: v for k, v in FIX.items() if "adr" not in k}
    s = statuses(run_gate(make_run(tmp_path / "a", commits=[BUGGY, no_adr]), "bug-hunt-and-fix"))
    assert s["ADR written"] == "FAIL"
    only_test = {"tests/test_regression.py": FIX["tests/test_regression.py"]}
    s = statuses(run_gate(make_run(tmp_path / "b", commits=[BUGGY, only_test]), "bug-hunt-and-fix"))
    assert s["fix + regression test present"] == "FAIL"
    assert s["regression test fails without the fix"] == "SKIP"
    sprawl = {**FIX, **{f"src/m{i}.py": "x = 1\n" for i in range(6)}}
    s = statuses(run_gate(make_run(tmp_path / "c", commits=[BUGGY, sprawl]), "bug-hunt-and-fix"))
    assert s["fix is focused"] == "FAIL"


# ── codebase-wiki ──────────────────────────────────────────────────────────

W = "docs/wiki"
BODY = "This page describes things in enough words to count as real content. " * 3
WIKI_OK = {
    f"{W}/index.md": f"# Index\n{BODY}\n- [Architecture](architecture.md)\n- [Glossary](glossary.md)\n"
                     "- [Auth](modules/auth.md)\n- [Login](flows/login.md)\n- [Site](https://example.com)\n",
    f"{W}/architecture.md": f"# Architecture\n{BODY}\n```mermaid\ngraph TD\n  A --> B\n```\n"
                            "Read next: [Auth](modules/auth.md)\n",
    f"{W}/modules/auth.md": f"# Auth\n{BODY}\nSee [login flow](../flows/login.md#steps).\n",
    f"{W}/flows/login.md": f"# Login\n{BODY}\n1. `app/auth.py:42` checks the password.\n",
    f"{W}/glossary.md": f"# Glossary\n{BODY}\n- **Alpha** - first.\n- **Beta** - second.\n- **Gamma** - third.\n",
    f"{W}/_inventory.md": f"# Inventory\n{BODY}\n",
}


def test_codebase_wiki_passes(tmp_path: Path) -> None:
    report = run_gate(make_run(tmp_path, WIKI_OK), "codebase-wiki")
    assert report.passed, report.render()
    assert not [c for c in report.checks if c.skipped]


def test_codebase_wiki_failures(tmp_path: Path) -> None:
    broken = dict(WIKI_OK)
    broken[f"{W}/architecture.md"] = f"# Architecture\n{BODY}\n[gone](modules/missing.md)\n"
    broken[f"{W}/glossary.md"] = f"# Glossary\n{BODY}\n- **Zed** - z.\n- **Alpha** - a.\n- **Beta** - b.\n"
    broken[f"{W}/flows/login.md"] = f"# Login\n{BODY}\nno citations here\n"
    broken[f"{W}/modules/orphan.md"] = f"# Orphan\n{BODY}\n"
    broken[f"{W}/modules/empty.md"] = "# x\n"
    s = statuses(run_gate(make_run(tmp_path, broken), "codebase-wiki"))
    assert s["architecture mermaid diagram"] == "FAIL"
    assert s["internal links resolve"] == "FAIL"
    assert s["glossary alphabetised"] == "FAIL"
    assert s["flow pages cite file:line"] == "FAIL"
    assert s["index reaches every page"] == "FAIL"
    assert s["no empty pages"] == "FAIL"
    assert s["wiki structure"] == "PASS"
    gone = {k: v for k, v in WIKI_OK.items() if "flows" not in k and "index" not in k}
    s = statuses(run_gate(make_run(tmp_path / "g", gone), "codebase-wiki"))
    assert s["wiki structure"] == "FAIL"


# ── fullstack-web-app ──────────────────────────────────────────────────────

FULL_OK = {
    "backend/__init__.py": "",
    "backend/app.py": ('ROUTES = {"/api/items": ["GET", "POST"]}\n\n'
                       "def list_items():\n    return []\n"),
    "frontend/index.html": ("<!doctype html><html><body><script>\n"
                            "fetch('/api/items').then(r => r.json());\n"
                            "fetch(`/api/items/${id}`);\n</script></body></html>\n"),
    "docs/api_contract.md": "# Contract\n\n- GET /api/items\n- POST /api/items\n- DELETE /api/items/{id}\n",
    "tests/test_app.py": "from backend.app import list_items\n\ndef test_list():\n    assert list_items() == []\n",
}


def test_fullstack_web_app_passes_with_server_boot_skipped(tmp_path: Path) -> None:
    report = run_gate(make_run(tmp_path, FULL_OK), "fullstack-web-app")
    assert report.passed, report.render()
    skipped = [c for c in report.checks if c.skipped]
    assert [c.name for c in skipped] == ["app boots and serves the frontend"]
    assert "running server" in skipped[0].detail


def test_fullstack_web_app_failures(tmp_path: Path) -> None:
    bad = {**FULL_OK,
           "frontend/index.html": "<html><script>fetch('/api/invented')</script></html>",
           "docs/api_contract.md": "# Contract\n\n- GET /api/nothing\n",
           "tests/test_app.py": "def test_boom():\n    assert 1 == 2\n"}
    s = statuses(run_gate(make_run(tmp_path / "a", bad), "fullstack-web-app"))
    assert s["frontend calls real endpoints"] == "FAIL"
    assert s["contract matches backend"] == "FAIL"
    assert s["integration tests pass"] == "FAIL"
    no_fetch = {**FULL_OK, "frontend/index.html": "<html><body>static</body></html>"}
    s = statuses(run_gate(make_run(tmp_path / "b", no_fetch), "fullstack-web-app"))
    assert s["frontend calls real endpoints"] == "FAIL"
    syntax = {**FULL_OK, "backend/app.py": "def broken(:\n"}
    del syntax["frontend/index.html"]
    s = statuses(run_gate(make_run(tmp_path / "c", syntax), "fullstack-web-app"))
    assert s["backend + frontend layout"] == "FAIL"


# ── Registry, report and CLI ───────────────────────────────────────────────


def test_every_example_project_has_functional_checks() -> None:
    assert sorted(functional.FUNCTIONAL_CHECKS) == PROJECTS
    for project in PROJECTS:
        assert (REPO_ROOT / "data" / "example_projects" / project / "template.json").is_file()
        assert len(functional.FUNCTIONAL_CHECKS[project]) >= 4, project


def test_render_marks_pass_fail_and_skip() -> None:
    report = GateReport("p", Path("/r"), [CheckResult("one", True, "fine"), CheckResult("two", False, "broke"),
                                          CheckResult("three", True, "why", skipped=True)])
    text = report.render()
    assert not report.passed and [c.name for c in report.failures] == ["two"]
    assert "PASS  one" in text and "FAIL  two" in text and "SKIP  three" in text
    assert text.splitlines()[-1] == "FAILED: 1 passed, 1 failed, 1 skipped"
    assert report.to_dict()["checks"][2]["status"] == "SKIP"


def test_cli_exit_codes_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = make_run(tmp_path / "good", API_OK)
    assert cli_main([str(good), "--project", "api-client-library"]) == 0
    assert "PASSED" in capsys.readouterr().out
    bad = make_run(tmp_path / "bad", board=[s for s in good_board() if s["type"] != "COMPLETE"])
    assert cli_main([str(bad)]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert cli_main([str(good), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True and len(payload["checks"]) == 10


def test_python_dash_m_and_wrapper_script(tmp_path: Path) -> None:
    run = make_run(tmp_path, API_OK)
    proc = subprocess.run([sys.executable, "-m", "tests.quality", str(run), "--project", "api-client-library"],
                          capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = subprocess.run([str(REPO_ROOT / "scripts" / "run_quality_gate.sh"), str(run), "api-client-library"],
                          capture_output=True, text=True, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "project=api-client-library" in proc.stdout
    proc = subprocess.run([str(REPO_ROOT / "scripts" / "run_quality_gate.sh"), str(tmp_path / "missing")],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    proc = subprocess.run([str(REPO_ROOT / "scripts" / "run_quality_gate.sh")], capture_output=True, text=True)
    assert proc.returncode == 2
