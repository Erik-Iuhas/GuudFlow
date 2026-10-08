"""Tests for core.ledger: the verification ledger (role contracts plan §4.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.ledger import (
    CheckInfo,
    build_ledger,
    confirm,
    counts,
    match_test,
    outcomes_from,
    parse_junit,
    spec_states,
    with_report,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "junit" / "bun.xml"

PYTEST_XML = """<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="3">
  <testcase classname="tests.test_dash.TestDash" name="test_charges" time="0.1"/>
  <testcase classname="tests.test_dash" name="test_refill[2]" time="0.1">
    <failure message="assert 1 == 2">boom</failure></testcase>
  <testcase classname="tests.test_dash" name="test_slow" time="0"><skipped/></testcase>
</testsuite></testsuites>"""

VITEST_XML = """<testsuites><testsuite name="src/dash.test.ts" tests="1">
  <testcase classname="src/dash.test.ts" name="Dash &gt; spends a charge" time="0.01"/>
</testsuite></testsuites>"""


@pytest.mark.parametrize(("command", "expect"), [
    ("bun test", "mkdir -p .guudflow && bun test --reporter=junit "
                 "--reporter-outfile=.guudflow/junit.xml"),
    ("cd web && pytest -q", "mkdir -p .guudflow && cd web && pytest --junitxml=.guudflow/junit.xml -q"),
    ("npx vitest run", "mkdir -p .guudflow && npx vitest run --reporter=default --reporter=junit "
                       "--outputFile=.guudflow/junit.xml"),
])
def test_known_runners_get_report_flags(command: str, expect: str) -> None:
    assert with_report(command) == (expect, ".guudflow/junit.xml")


def test_unknown_runner_and_explicit_report_are_left_alone() -> None:
    assert with_report("make check") == ("make check", None)
    assert with_report("pytest --junitxml=out.xml") == ("pytest --junitxml=out.xml", None)


def test_parses_bun_report_with_describe_paths() -> None:
    results = parse_junit(FIXTURE.read_text())
    keys = [r.key for r in results]
    assert "tests/maze.test.js::classic1 parsing > exact dimensions" in keys
    assert all(r.status == "passed" for r in results)
    assert match_test("tests/maze.test.js::exact dimensions", results)
    assert match_test("tests/maze.test.js::classic1 parsing > exact dimensions", results)
    assert len(match_test("tests/rng.test.js", results)) >= 1          # file-level link
    assert not match_test("tests/maze.test.js::no such test", results)


def test_parses_pytest_and_vitest() -> None:
    py = parse_junit(PYTEST_XML)
    assert [(r.file, r.full, r.status) for r in py] == [
        ("tests/test_dash.py", "TestDash > test_charges", "passed"),
        ("tests/test_dash.py", "test_refill[2]", "failed"),
        ("tests/test_dash.py", "test_slow", "skipped")]
    assert match_test("tests/test_dash.py::TestDash::test_charges", py)
    assert match_test("tests/test_dash.py::test_refill", py)               # parametrized
    vi = parse_junit(VITEST_XML)
    assert vi[0].file == "src/dash.test.ts" and match_test("src/dash.test.ts::Dash > spends a charge", vi)
    assert parse_junit("not xml") == []


def test_ledger_states() -> None:
    results = parse_junit(PYTEST_XML)
    checks = [CheckInfo("R1.1", "unit", "tests/test_dash.py::TestDash::test_charges"),
              CheckInfo("R1.2", "unit", "tests/test_dash.py::test_refill"),
              CheckInfo("R1.3", "unit", "tests/test_dash.py::test_gone"),
              CheckInfo("R1.4", "manual", None),
              CheckInfo("R1.5", "unit", None),
              CheckInfo("R1.6", "manual", None)]
    ledger = build_ledger(checks, results, {"R1.2": True, "R1.4": True, "R1.5": False})
    assert {k: v.state for k, v in ledger.items()} == {
        "R1.1": "verified", "R1.2": "failing", "R1.3": "missing_test", "R1.4": "claimed",
        "R1.5": "unmet", "R1.6": "unchecked"}
    # A reviewer's "met" does not outweigh a failing test.
    assert ledger["R1.2"].detail.startswith("failed:")
    # No per-test report: linked tests cannot prove anything, the reviewer's word stands.
    blind = build_ledger(checks[:1], None, {"R1.1": True})
    assert blind["R1.1"].state == "claimed" and "no per-test results" in blind["R1.1"].detail


def test_test_first_needs_red_before_green() -> None:
    after = outcomes_from({"tests/a.test.js::adds": "passed", "tests/a.test.js::keeps": "passed"})
    before = outcomes_from({"tests/a.test.js::adds": "failed", "tests/a.test.js::keeps": "passed"})
    checks = [CheckInfo("R1.1", "unit", "tests/a.test.js::adds"),
              CheckInfo("R1.2", "unit", "tests/a.test.js::keeps")]
    ledger = build_ledger(checks, after, {"R1.2": True}, before=before)
    assert ledger["R1.1"].state == "verified"
    assert ledger["R1.2"].state == "claimed" and "already passed" in ledger["R1.2"].detail


def test_person_confirmation_and_spec_states() -> None:
    ledger = {k: v.to_dict() for k, v in build_ledger(
        [CheckInfo("R1.1", "manual", None), CheckInfo("R1.2", "unit", "t.py::x")],
        outcomes_from({"t.py::x": "failed"}), {}).items()}
    out = confirm(ledger, ["R1.1", "R1.2", "R9.9"], "erik")
    assert out["R1.1"]["state"] == "person" and out["R1.1"]["detail"] == "confirmed by erik"
    assert out["R1.2"]["state"] == "failing"                    # a failing test is not overridden
    assert out["R9.9"]["state"] == "person"
    assert counts(out) == {"person": 2, "failing": 1}
    assert spec_states(out) == {"R1.1": (True, "person"), "R1.2": (False, "failing"),
                                "R9.9": (True, "person")}


def test_a_link_to_a_describe_block_names_every_test_in_it() -> None:
    from core.ledger import TestOutcome, match_test
    results = [TestOutcome(file="tests/blast.test.js", name="L1: kills within 2",
                           full="R4.8: Ghost Blast kills > L1: kills within 2", status="passed"),
               TestOutcome(file="tests/blast.test.js", name="L2: kills within 3",
                           full="outer > R4.8: Ghost Blast kills > L2: kills within 3", status="passed"),
               TestOutcome(file="tests/blast.test.js", name="other",
                           full="data > other", status="passed")]
    found = match_test("tests/blast.test.js::R4.8: Ghost Blast kills", results)
    assert [r.name for r in found] == ["L1: kills within 2", "L2: kills within 3"]
