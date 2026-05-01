"""tech_lead LEAD-AT-THE-TOP mode is present in the rendered system prompt."""
from agents.builtin.tech_lead import TECH_LEAD_SYSTEM_PROMPT


def test_lead_at_top_text_present():
    assert "LEAD-AT-THE-TOP MODE" in TECH_LEAD_SYSTEM_PROMPT
    assert "DO IT YOURSELF FIRST" in TECH_LEAD_SYSTEM_PROMPT


def test_delegation_tilt_present():
    assert "PREFER OWN WORK OVER DELEGATION" in TECH_LEAD_SYSTEM_PROMPT
