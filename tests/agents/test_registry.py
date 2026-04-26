"""Tests for agents/registry.py."""

from __future__ import annotations

import pytest

from agents.base import BaseAgent
from agents.registry import _Registry, get, has, register, registry


@pytest.fixture(autouse=True)
def _reset_registry():
    snapshot = dict(registry._classes)
    yield
    registry._classes.clear()
    registry._classes.update(snapshot)


class _AgentA(BaseAgent):
    pass


class _AgentB(BaseAgent):
    pass


def test_register_and_get():
    r = _Registry()
    r.register("a", _AgentA)
    assert r.get("a") is _AgentA
    assert r.has("a")


def test_get_unknown_raises():
    r = _Registry()
    with pytest.raises(KeyError):
        r.get("nothing")


def test_register_duplicate_raises():
    r = _Registry()
    r.register("a", _AgentA)
    with pytest.raises(ValueError, match="already registered"):
        r.register("a", _AgentB)


def test_register_non_baseagent_raises():
    r = _Registry()

    class NotAnAgent:
        pass

    with pytest.raises(TypeError):
        r.register("x", NotAnAgent)  # type: ignore[arg-type]


def test_register_empty_role_raises():
    r = _Registry()
    with pytest.raises(ValueError):
        r.register("", _AgentA)


def test_module_level_helpers():
    register("module_a", _AgentA)
    assert has("module_a")
    assert get("module_a") is _AgentA


def test_all_role_types_sorted():
    r = _Registry()
    r.register("zebra", _AgentA)
    r.register("apple", _AgentB)
    assert r.all_role_types() == ["apple", "zebra"]
