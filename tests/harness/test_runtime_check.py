"""Before a session: the project's test runners, and whether agents can run them (F3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.harness import runtime_check
from core.harness.capabilities import needed_runners
from core.spec import Rule
from core.work_db import WorkDB


def test_needed_runners_from_the_check_command_steps() -> None:
    assert needed_runners("CI=1 bun test && cd web && npx vitest run | tee out", []) == {
        "bun": "the check command `CI=1 bun test && cd web && npx vitest run | tee out`",
        "npx": "the check command `CI=1 bun test && cd web && npx vitest run | tee out`",
        "tee": "the check command `CI=1 bun test && cd web && npx vitest run | tee out`"}
    assert needed_runners("", []) == {}


def test_needed_runners_from_rules_name_test_runners_not_prose() -> None:
    rules = [("rule-1", "Testing framework: Use bun test"),
             ("rule-2", "Every node in the graph has a parent; keep Python 3.11 syntax"),
             ("rule-3", "Run pytest before merging")]
    assert needed_runners("", rules) == {
        "bun": "spec rule rule-1: Testing framework: Use bun test",
        "pytest": "spec rule rule-3: Run pytest before merging"}


def test_the_check_command_reason_wins_over_a_rule() -> None:
    assert needed_runners("bun test", [("rule-1", "Use bun test")])["bun"].startswith("the check")


def _project(tmp_path: Path, *rules: Rule) -> Path:
    db = WorkDB(tmp_path / "projects" / "p1")
    with db.spec_doc() as doc:
        doc["rules"] = [r.to_dict() for r in rules]
    return tmp_path


def test_host_mode_names_runners_missing_from_path(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path, Rule(id="rule-1", text="Use bun test", source="human"),
                    Rule(id="rule-2", text="Use npm test", source="human", status="dropped"))
    monkeypatch.setattr(runtime_check.shutil, "which",
                        lambda prog: None if prog == "bun" else f"/usr/bin/{prog}")
    found = runtime_check.missing_runtimes(root, "p1", "pytest -q", sandbox="host")
    assert [m.to_dict() for m in found] == [
        {"program": "bun", "why": "spec rule rule-1: Use bun test", "where": "this machine"}]


def test_nothing_named_means_nothing_missing(tmp_path: Path) -> None:
    assert runtime_check.missing_runtimes(_project(tmp_path), "p1", "", sandbox="host") == []


def test_docker_mode_asks_the_sandbox_image(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from worker import sandbox_manager

    asked: list[list[str]] = []

    def missing(self: object, programs: list[str]) -> list[str] | None:
        asked.append(programs)
        return ["pytest"]

    monkeypatch.setattr(sandbox_manager.SandboxManager, "available", staticmethod(lambda: True))
    monkeypatch.setattr(sandbox_manager.SandboxManager, "missing_programs", missing)
    found = runtime_check.missing_runtimes(_project(tmp_path), "p1", "pytest -q", sandbox="auto")
    assert asked == [["pytest"]]
    assert [(m.program, m.where) for m in found] == [("pytest", "the sandbox image")]

    monkeypatch.setattr(sandbox_manager.SandboxManager, "missing_programs",
                        lambda self, programs: None)
    assert runtime_check.missing_runtimes(_project(tmp_path), "p1", "pytest -q",
                                          sandbox="auto") == []
