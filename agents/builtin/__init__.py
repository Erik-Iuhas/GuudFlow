"""Builtin agent registration.

`register_builtins()` is the single entry point that wires every
builtin into `agents/registry.py`. Idempotent — calling twice is a
no-op so the API can call it at startup AND tests can call it without
fighting prior registration.

Phase C.0 adds the abstract bases (`agents/abstract.py`) and refactors
existing builtins to extend them; this module also gains the
`RequirementsWriter` specialist as the proof-of-pattern.

Synthesis agents (`MemoryWriter`, `VisualChecker`, `TestRunner`,
`ITAgent`) are NOT auto-registered here — they extend `BaseAgent`
directly and are intentionally outside the catalog taxonomy until a
fit emerges.
"""

from __future__ import annotations

from agents.builtin.code_reviewer import CodeReviewer
from agents.builtin.requirements_writer import RequirementsWriter
from agents.builtin.tech_lead import TechLead
from agents.registry import registry


_BUILTIN_ARCHETYPES = (
    ("code_reviewer", CodeReviewer),
    ("tech_lead", TechLead),
    ("requirements_writer", RequirementsWriter),
)


def register_builtins() -> None:
    """Register every catalog-able builtin. Safe to call multiple times."""
    for role_type, cls in _BUILTIN_ARCHETYPES:
        if not registry.has(role_type):
            registry.register(role_type, cls)
