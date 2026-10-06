"""Layer selection of the project manager's system prompt."""

from __future__ import annotations

import pytest

from core.pm_prompts import (
    LEAD_EMPTY, LEAD_HELD, MODES, PHASES, REVIEWER_ALL, REVIEWER_MUST, START_MODES, Seats,
    normalise_mode, pm_system_prompt)


def _prompt(seats: Seats = Seats(), mode: str = "spec_first", phase: str = "plan") -> str:
    return pm_system_prompt(seats=seats, start_mode=mode, phase=phase)  # type: ignore[arg-type]


def test_seats_parse_missing_and_unknown_values() -> None:
    assert Seats.from_project(None) == Seats(lead=False, reviewer="off")
    assert Seats.from_project({}) == Seats()
    assert Seats.from_project({"seats": "bogus"}) == Seats()
    assert Seats.from_project({"seats": {"lead": "yes", "reviewer": "everyone"}}) == Seats()
    assert Seats.from_project({"seats": {"lead": True, "reviewer": "must"}}) == Seats(True, "must")


def test_base_is_always_present() -> None:
    for phase in PHASES:
        text = _prompt(phase=phase)
        assert "project manager" in text and "submit_plan_turn" in text
        assert "never apply" in text and "one blocking question" in text
        assert "`serves`" in text


def test_lead_empty_is_the_tech_lead_stance() -> None:
    text = _prompt(Seats(lead=False))
    assert LEAD_EMPTY in text and LEAD_HELD not in text
    assert "technical lead" in text and "rejected" in text
    assert "`research` work item" in text and "reversible" in text and "binding" in text


def test_lead_held_proposes_options() -> None:
    text = _prompt(Seats(lead=True))
    assert LEAD_HELD in text and LEAD_EMPTY not in text
    assert "trade-offs" in text and "recommend" in text
    assert "Do not mark a decision `binding`" in text


@pytest.mark.parametrize("reviewer,expected,absent", [
    ("off", None, (REVIEWER_ALL, REVIEWER_MUST)),
    ("all", REVIEWER_ALL, (REVIEWER_MUST,)),
    ("must", REVIEWER_MUST, (REVIEWER_ALL,)),
])
def test_reviewer_seat(reviewer: str, expected: str | None, absent: tuple[str, ...]) -> None:
    text = _prompt(Seats(reviewer=reviewer))
    if expected:
        assert expected in text and "small enough to review" in text
    else:
        assert "small enough to review" not in text
    assert all(a not in text for a in absent)


def test_each_start_mode_has_its_own_paragraph() -> None:
    assert set(MODES) == set(START_MODES)
    for mode in START_MODES:
        text = _prompt(mode=mode)
        assert MODES[mode] in text
        assert all(MODES[o] not in text for o in START_MODES if o != mode)


def test_unknown_mode_is_spec_first() -> None:
    assert normalise_mode("nonsense") == "spec_first" and normalise_mode(None) == "spec_first"
    assert MODES["spec_first"] in _prompt(mode="nonsense")


def test_each_phase_has_its_own_paragraph() -> None:
    for phase, para in PHASES.items():
        text = _prompt(phase=phase)
        assert para in text
        assert all(PHASES[o] not in text for o in PHASES if o != phase)
    assert "report" in PHASES["after_session"]
    assert "requirements from what was built" in PHASES["derive_spec"]
