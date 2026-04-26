"""Tests for the abstract agent archetype layer (Phase C.0).

Verifies:
  - The intermediate bases (Reviewer, EscalationHandler, Specialist)
    set the archetype_kind discriminator correctly
  - Concrete subclasses inherit the right defaults
  - Concrete subclasses can override defaults
  - All concrete subclasses also work as BaseAgent (back-compat)
"""

from __future__ import annotations

from agents.abstract import (
    AgentArchetype,
    EscalationHandler,
    Reviewer,
    Specialist,
)
from agents.base import BaseAgent
from agents.builtin.architect import Architect
from agents.builtin.code_reviewer import CodeReviewer
from agents.builtin.requirements_writer import RequirementsWriter
from agents.builtin.tech_lead import TechLead
from core.signals import SignalType


# ── Bases ──────────────────────────────────────────────────────────────────


def test_reviewer_base_has_correct_kind():
    assert Reviewer.archetype_kind == "reviewer"


def test_escalation_handler_base_has_correct_kind():
    assert EscalationHandler.archetype_kind == "escalation_handler"


def test_specialist_base_has_correct_kind():
    assert Specialist.archetype_kind == "specialist"


def test_intermediate_base_provides_default_tools():
    """Reviewer's class-level default_tools includes the read tools every
    reviewer needs."""
    assert "git:diff" in Reviewer.default_tools
    assert "fs:read" in Reviewer.default_tools


def test_escalation_handler_defaults_to_heavy_tier():
    """Escalations are HEAVY by default per CLAUDE.md / DESIGN §22."""
    assert EscalationHandler.default_model_tier == "heavy"


# ── Concrete subclasses inherit from the right base ───────────────────────


def test_code_reviewer_extends_reviewer():
    assert issubclass(CodeReviewer, Reviewer)
    assert CodeReviewer.archetype_kind == "reviewer"
    # Inherits the parent's tool list verbatim — CodeReviewer doesn't
    # override (yet).
    assert "git:diff" in CodeReviewer.default_tools


def test_tech_lead_extends_escalation_handler():
    assert issubclass(TechLead, EscalationHandler)
    assert TechLead.archetype_kind == "escalation_handler"
    assert TechLead.default_model_tier == "heavy"
    assert TechLead.wake_on_signal_type is SignalType.REJECTED


def test_requirements_writer_extends_specialist():
    assert issubclass(RequirementsWriter, Specialist)
    assert RequirementsWriter.archetype_kind == "specialist"
    # RequirementsWriter overrides default_tools to include doc + write
    # — proves class-level overrides flow through.
    assert "fs:write" in RequirementsWriter.default_tools
    assert "doc:pdf" in RequirementsWriter.default_tools
    assert "doc:markdown" in RequirementsWriter.default_tools
    assert "fs:write" in RequirementsWriter.default_rbac_scopes


def test_requirements_writer_does_not_grant_code_exec():
    """Spot-check the opinionated permission posture: RequirementsWriter
    reads + writes docs, doesn't run code."""
    assert "code:exec" not in RequirementsWriter.default_tools
    assert "code:exec" not in RequirementsWriter.default_rbac_scopes


# ── All concrete subclasses are still BaseAgent ───────────────────────────


def test_concrete_archetypes_all_extend_base_agent():
    for cls in (CodeReviewer, TechLead, RequirementsWriter, Architect):
        assert issubclass(cls, BaseAgent), cls
        assert issubclass(cls, AgentArchetype), cls


def test_concrete_archetypes_construct_with_defaults():
    """Each concrete subclass constructs without error using just role
    + the kwargs BaseAgent expects. Proves the inheritance chain
    doesn't add required positional args."""
    for cls in (CodeReviewer, TechLead, RequirementsWriter, Architect):
        agent = cls()
        assert isinstance(agent, BaseAgent)
        assert agent.role  # role is set by the subclass


# ── Architect (Phase C.4) ──────────────────────────────────────────────────


def test_architect_extends_reviewer():
    assert issubclass(Architect, Reviewer)
    assert Architect.archetype_kind == "reviewer"


def test_architect_defaults_to_heavy_tier():
    """Architects spend real tokens — HEAVY by default per CLAUDE.md."""
    assert Architect.default_model_tier == "heavy"


def test_architect_inherits_reviewer_tools():
    """Architect doesn't override default_tools — it gets the standard
    diff-aware reviewer toolkit from Reviewer."""
    assert "git:diff" in Architect.default_tools
    assert "git:log" in Architect.default_tools
    assert "fs:read" in Architect.default_tools
