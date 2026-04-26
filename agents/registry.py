"""Registry — role name → agent class.

DESIGN §3.4 / hard rule 4: "BaseAgent is the only contract. Runner imports
agents through `agents/registry.py` only." This module is the single
import point.

Stage 4 ships an empty registry; Stage 5's builtins register themselves.
Custom agents (in `extensions/`) register via the same API.
"""

from __future__ import annotations

from typing import Type

from agents.base import BaseAgent


class _Registry:
    def __init__(self) -> None:
        self._classes: dict[str, Type[BaseAgent]] = {}

    def register(self, role_type: str, cls: Type[BaseAgent]) -> None:
        if not role_type:
            raise ValueError("role_type must be non-empty")
        if not issubclass(cls, BaseAgent):
            raise TypeError(f"{cls!r} must be a subclass of BaseAgent")
        if role_type in self._classes:
            raise ValueError(f"role_type {role_type!r} already registered")
        self._classes[role_type] = cls

    def get(self, role_type: str) -> Type[BaseAgent]:
        if role_type not in self._classes:
            raise KeyError(f"no agent class registered for {role_type!r}")
        return self._classes[role_type]

    def has(self, role_type: str) -> bool:
        return role_type in self._classes

    def all_role_types(self) -> list[str]:
        return sorted(self._classes.keys())

    def clear(self) -> None:
        """Test-only — reset the registry between tests."""
        self._classes.clear()


# Module-level singleton. Per CLAUDE.md "no global state except job queue
# instance and registry."
registry = _Registry()


def register(role_type: str, cls: Type[BaseAgent]) -> None:
    registry.register(role_type, cls)


def get(role_type: str) -> Type[BaseAgent]:
    return registry.get(role_type)


def has(role_type: str) -> bool:
    return registry.has(role_type)
