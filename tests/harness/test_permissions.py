"""Tests for core.harness.permissions."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

import pytest

from core.harness.capabilities import (
    PROTECTED_PATHS, ROLE_TYPES, Capabilities, compile_capabilities, defaults_for,
)
from core.harness.permissions import MCP_PERMISSION_KEY, build_permission, to_ruleset


def _perm(role: str, caps: Capabilities | None = None) -> dict[str, Any]:
    return build_permission(compile_capabilities(caps or defaults_for(role), role))


def decide(rules: Any, value: str) -> str:
    """opencode semantics: last matching pattern wins; no match -> deny."""
    if isinstance(rules, str):
        return rules
    result = "deny"
    for pattern, action in rules.items():
        if fnmatchcase(value, pattern):
            result = action
    return result


@pytest.mark.parametrize("role", ROLE_TYPES)
def test_base_shape(role: str) -> None:
    p = _perm(role)
    assert next(iter(p.items())) == ("*", "deny")
    assert p["StructuredOutput"] == "allow"
    for key in ("webfetch", "websearch", "external_directory", "question", "doom_loop"):
        assert p[key] == "deny"
    assert p[MCP_PERMISSION_KEY] == "allow"
    for key in ("read", "glob", "grep", "list", "lsp", "todowrite"):
        assert p[key] == "allow"


@pytest.mark.parametrize("role", ROLE_TYPES)
def test_deny_patterns_after_allows(role: str) -> None:
    p = _perm(role)
    for key in ("edit", "bash"):
        rules = p[key]
        if isinstance(rules, str):
            continue
        keys = list(rules)
        assert keys[0] == "*"
        allows = [i for i, k in enumerate(keys) if rules[k] == "allow"]
        denies = [i for i, k in enumerate(keys) if i > 0 and rules[k] == "deny"]
        if allows and denies:
            assert min(denies) > max(allows)


def test_reviewer() -> None:
    p = _perm("reviewer")
    assert p["edit"] == "deny"
    assert p["bash"]["pytest*"] == "allow"
    assert "git commit*" not in p["bash"]
    assert decide(p["bash"], "git commit -m x") == "deny"


def test_builder_edit_and_bash() -> None:
    p = _perm("builder")
    keys = list(p["edit"])
    assert p["edit"]["**"] == "allow"
    for path in PROTECTED_PATHS:
        assert p["edit"][path] == "deny"
        assert keys.index(path) > keys.index("**")
    assert decide(p["edit"], "src/app.py") == "allow"
    assert decide(p["edit"], "inputs/spec.md") == "deny"
    assert decide(p["edit"], "opencode.json") == "deny"
    assert decide(p["bash"], "git push origin main") == "deny"
    assert decide(p["bash"], "pytest -q") == "allow"
    assert decide(p["bash"], "rm -rf /") == "deny"


def test_shell_ask() -> None:
    assert _perm("builder", Capabilities(sandbox="standard", shell="ask"))["bash"]["*"] == "ask"


def test_task_rendering() -> None:
    assert _perm("builder", Capabilities(sandbox="standard", subagents="off"))["task"] == "deny"
    assert _perm("builder", Capabilities(sandbox="standard", subagents="explore_only"))["task"] == {
        "*": "deny", "explore": "allow"}
    assert _perm("builder", Capabilities(sandbox="standard", subagents="full"))["task"] == "allow"


def test_to_ruleset() -> None:
    p = _perm("builder")
    rs = to_ruleset(p)
    assert rs[0] == {"permission": "*", "pattern": "*", "action": "deny"}
    assert {"permission": "read", "pattern": "*", "action": "allow"} in rs
    total = sum(1 if isinstance(v, str) else len(v) for v in p.values())
    assert len(rs) == total
    assert [r["permission"] for r in rs] == [
        k for k, v in p.items() for _ in ([0] if isinstance(v, str) else v)]
    assert [r["pattern"] for r in rs if r["permission"] == "edit"] == list(p["edit"])


@pytest.mark.parametrize("role", ["builder", "reviewer", "tester"])
def test_bun_runs_tests_but_never_installs(role: str) -> None:
    """The sandbox's `bun` shim may run tests and scripts; package installs stay denied."""
    bash = _perm(role)["bash"]
    assert decide(bash, "bun test") == "allow"
    assert decide(bash, "bun test tests/game.test.js") == "allow"
    assert decide(bash, "bun run build.js") == "allow"
    for cmd in ("bun install", "bun add left-pad", "bun i left-pad", "bunx cowsay hi",
                "bun x cowsay", "bun upgrade", "bun pm cache", "bun create vite"):
        assert decide(bash, cmd) == "deny", cmd
