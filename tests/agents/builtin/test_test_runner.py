"""Tests for agents/builtin/test_runner.py."""

from __future__ import annotations

from agents.builtin.test_runner import parse_test_output


def test_parse_pytest_output():
    stdout = """
=========== test session starts ===========
tests/test_x.py ......
======= 6 passed in 0.05s =======
"""
    res = parse_test_output(stdout)
    assert res["framework"] == "pytest"
    assert res["passed"] == 6


def test_parse_pytest_with_failures():
    stdout = "===== 2 failed, 8 passed in 1.5s ====="
    res = parse_test_output(stdout)
    assert res["framework"] == "pytest"
    assert res["failed"] == 2
    assert res["passed"] == 8


def test_parse_jest_output():
    stdout = """
PASS  src/auth.test.js
Tests:       1 failed, 5 passed, 6 total
"""
    res = parse_test_output(stdout)
    assert res["framework"] == "jest"
    assert res["failed"] == 1
    assert res["passed"] == 5


def test_parse_unknown_framework():
    res = parse_test_output("nothing meaningful here")
    assert res["framework"] == "unknown"
