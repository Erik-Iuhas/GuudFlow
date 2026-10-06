"""Tests for core.harness.overseer."""
import json
from typing import Any

import pytest

from core.harness.overseer import (
    NEEDS_HUMAN,
    OverseerCaps,
    OverseerResult,
    TeamView,
    check_interventions,
    intervention_signal,
    overseer_json_schema,
    validate_overseer,
)
from core.harness.results import validate_result
from core.signals import SignalType

ASSESS = "The builder has looped on the same failing test for three rounds."
WHY = "This unblocks the chain without a human."

TEAM = TeamView(
    roles={
        "builder": "builder",
        "builder2": "builder",
        "reviewer": "reviewer",
        "overseer": "overseer",
    },
    tasks={"T1": "builder", "T2": "reviewer"},
    review_edges={"builder": "reviewer"},
    review_rounds={"builder": (2, 3)},
    restarts_used={"builder": 1},
)
CAPS = OverseerCaps()


def result(*ivs: dict[str, Any]) -> dict[str, Any]:
    return {"assessment": ASSESS, "interventions": [{"rationale": WHY, **iv} for iv in ivs]}


def check(*ivs: dict[str, Any], team: TeamView = TEAM, caps: OverseerCaps = CAPS) -> list[str]:
    parsed, err = validate_result_only(result(*ivs))
    assert parsed is not None, err
    return check_interventions(parsed, team, caps)


def validate_result_only(raw: Any) -> tuple[OverseerResult | None, str | None]:
    return validate_result(OverseerResult, raw)


REBRIEF = {"type": "rebrief", "role": "builder", "addendum": "Use the existing helper, do not rewrite it."}
DECIDE = {"type": "decide", "question": "Which db do we use?", "decision": "Use sqlite for now."}
REASSIGN = {"type": "reassign", "task_id": "T1", "from_role": "builder", "to_role": "builder2"}
SPLIT = {
    "type": "split",
    "task_id": "T1",
    "parts": [
        {"title": "Part one", "acceptance": ["a"]},
        {"title": "Part two", "acceptance": ["b"]},
    ],
}
EXTEND = {"type": "extend_review", "author_role": "builder", "reviewer_role": "reviewer"}
RESTART = {"type": "restart_role", "role": "builder2"}
PAUSE = {"type": "pause", "question": "Should we drop the legacy API?"}
TEAM_CHANGE = {
    "type": "propose_team_change",
    "change": {"add_role": {"name": "docs", "type": "writer"}},
    "summary": "Add a docs writer to the team.",
}


# --- pass: one per intervention type -------------------------------------


@pytest.mark.parametrize(
    "iv",
    [REBRIEF, DECIDE, REASSIGN, SPLIT, EXTEND, RESTART, PAUSE, TEAM_CHANGE],
    ids=lambda iv: iv["type"],
)
def test_each_intervention_passes(iv: dict[str, Any]) -> None:
    res, err = validate_overseer(result(iv), TEAM, CAPS)
    assert err is None
    assert res is not None and res.interventions[0].type == iv["type"]


def test_empty_interventions_ok() -> None:
    res, err = validate_overseer({"assessment": ASSESS}, TEAM, CAPS)
    assert err is None and res is not None and res.interventions == []


def test_restart_default_ref_is_main() -> None:
    res, _ = validate_overseer(result(RESTART), TEAM, CAPS)
    assert res is not None and res.interventions[0].from_ref == "main"  # type: ignore[union-attr]


# --- fail: one per intervention type -------------------------------------


def test_rebrief_fails_unknown_role_and_task() -> None:
    problems = check({**REBRIEF, "role": "ghost", "task_id": "T9"})
    assert any("interventions[0]" in p and "unknown role 'ghost'" in p for p in problems)
    assert any("unknown task 'T9'" in p for p in problems)


def test_decide_fails_unknown_task() -> None:
    assert any("unknown task 'T9'" in p for p in check({**DECIDE, "task_id": "T9"}))


def test_decide_with_known_task_ok() -> None:
    assert check({**DECIDE, "task_id": "T1"}) == []


def test_reassign_fails_wrong_owner() -> None:
    problems = check({**REASSIGN, "from_role": "reviewer", "to_role": "reviewer"})
    assert any("owned by 'builder'" in p for p in problems)


def test_reassign_fails_same_role() -> None:
    problems = check({**REASSIGN, "to_role": "builder"})
    assert any("must differ" in p for p in problems)


def test_reassign_fails_across_role_types() -> None:
    problems = check({**REASSIGN, "to_role": "reviewer"})
    assert any("role types differ" in p for p in problems)


def test_split_fails_unknown_task() -> None:
    assert any("unknown task 'T9'" in p for p in check({**SPLIT, "task_id": "T9"}))


def test_split_part_count_is_schema_checked() -> None:
    one = {**SPLIT, "parts": SPLIT["parts"][:1]}
    res, err = validate_overseer(result(one), TEAM, CAPS)
    assert res is None and err is not None
    five = {**SPLIT, "parts": SPLIT["parts"] * 3}
    res, err = validate_overseer(result(five), TEAM, CAPS)
    assert res is None and err is not None


def test_extend_review_fails_missing_edge() -> None:
    problems = check({**EXTEND, "author_role": "builder2"})
    assert any("no review edge" in p for p in problems)


def test_extend_review_hard_cap() -> None:
    team = TeamView(
        roles=TEAM.roles,
        tasks=TEAM.tasks,
        review_edges=TEAM.review_edges,
        review_rounds={"builder": (5, 5)},
    )
    assert any("hard cap" in p for p in check(EXTEND, team=team))
    ok_team = TeamView(
        roles=TEAM.roles,
        tasks=TEAM.tasks,
        review_edges=TEAM.review_edges,
        review_rounds={"builder": (4, 4)},
    )
    assert check(EXTEND, team=ok_team) == []


def test_restart_role_cap() -> None:
    problems = check({**RESTART, "role": "builder"})
    assert any("already used" in p for p in problems)


def test_pause_fails_when_two() -> None:
    problems = check(PAUSE, PAUSE)
    assert any("at most one pause" in p for p in problems)


def test_pause_short_question_rejected_by_schema() -> None:
    res, err = validate_overseer(result({**PAUSE, "question": "why?"}), TEAM, CAPS)
    assert res is None and err is not None and "question" in err


def test_team_change_fails_if_it_mentions_overseer() -> None:
    bad = {**TEAM_CHANGE, "change": {"grant": {"role": "overseer", "web": "on"}}}
    assert any("overseer" in p for p in check(bad))


# --- caps and cross-cutting rules ----------------------------------------


def test_too_many_per_wake() -> None:
    problems = check(REBRIEF, DECIDE, EXTEND, SPLIT)
    assert any("at most 3" in p for p in problems)
    assert check(REBRIEF, DECIDE, EXTEND) == []


def test_custom_cap_respected() -> None:
    assert any("at most 1" in p for p in check(REBRIEF, DECIDE, caps=OverseerCaps(max_per_wake=1)))


def test_overseer_cannot_name_itself() -> None:
    assert any("cannot act on itself" in p for p in check({**REBRIEF, "role": "overseer"}))
    assert any("cannot act on itself" in p for p in check({**RESTART, "role": "overseer"}))


def test_problem_lines_name_the_index() -> None:
    problems = check(DECIDE, {**REBRIEF, "role": "ghost"})
    assert problems and all("interventions[1]" in p for p in problems)


def test_validate_overseer_joins_problems_and_reports_schema_errors() -> None:
    res, err = validate_overseer(result({**REBRIEF, "role": "ghost"}), TEAM, CAPS)
    assert res is None and err is not None and "unknown role" in err
    res, err = validate_overseer({"assessment": "short"}, TEAM, CAPS)
    assert res is None and err is not None and "assessment" in err
    res, err = validate_overseer(result({"type": "teleport"}), TEAM, CAPS)
    assert res is None and err is not None
    res, err = validate_overseer(None, TEAM, CAPS)
    assert res is None and err is not None


def test_extra_fields_forbidden() -> None:
    res, err = validate_overseer(result({**PAUSE, "sneaky": 1}), TEAM, CAPS)
    assert res is None and err is not None and "unexpected field" in err


def test_short_rationale_rejected() -> None:
    raw = {"assessment": ASSESS, "interventions": [{**PAUSE, "rationale": "nope"}]}
    res, err = validate_overseer(raw, TEAM, CAPS)
    assert res is None and err is not None and "rationale" in err


def test_caps_defaults() -> None:
    c = OverseerCaps()
    assert (
        c.max_per_wake,
        c.max_wakes_per_run,
        c.max_restarts_per_role,
        c.review_round_hard_cap,
        c.wake_cooldown_seconds,
    ) == (3, 5, 1, 5, 120)


def test_needs_human_set() -> None:
    assert NEEDS_HUMAN == frozenset({"pause", "propose_team_change"})


# --- signal ---------------------------------------------------------------


def test_signal_shape_rebrief() -> None:
    res, _ = validate_overseer(result(REBRIEF), TEAM, CAPS)
    assert res is not None
    sig = intervention_signal(res.interventions[0], iteration=2)
    assert sig.type is SignalType.OVERSEER_INTERVENTION
    assert sig.from_agent == "overseer" and sig.iteration == 2
    assert sig.summary == f"rebrief builder: {REBRIEF['addendum'][:60]}"
    assert sig.detail is not None
    detail = json.loads(sig.detail)
    assert detail["needs_human"] is False
    assert detail["type"] == "rebrief" and detail["role"] == "builder"
    assert detail["rationale"] == WHY


def test_signal_truncates_to_60_chars() -> None:
    long = {**REBRIEF, "addendum": "x" * 100}
    res, _ = validate_overseer(result(long), TEAM, CAPS)
    assert res is not None
    assert intervention_signal(res.interventions[0]).summary == "rebrief builder: " + "x" * 60


@pytest.mark.parametrize(
    "iv", [REBRIEF, DECIDE, REASSIGN, SPLIT, EXTEND, RESTART, PAUSE, TEAM_CHANGE], ids=lambda iv: iv["type"]
)
def test_signal_for_every_type(iv: dict[str, Any]) -> None:
    res, _ = validate_overseer(result(iv), TEAM, CAPS)
    assert res is not None
    sig = intervention_signal(res.interventions[0])
    assert sig.summary.startswith(iv["type"] + " ")
    assert json.loads(sig.detail or "")["needs_human"] is (iv["type"] in NEEDS_HUMAN)


# --- schema ---------------------------------------------------------------


def test_schema_has_no_refs_and_keeps_union() -> None:
    schema = overseer_json_schema()
    assert "$ref" not in json.dumps(schema)
    assert "$defs" not in schema
    items = schema["properties"]["interventions"]["items"]
    branches = items.get("oneOf") or items.get("anyOf")
    assert branches is not None and len(branches) == 8
    types = {b["properties"]["type"]["const"] for b in branches}
    assert types == {
        "rebrief",
        "decide",
        "reassign",
        "split",
        "extend_review",
        "restart_role",
        "pause",
        "propose_team_change",
    }
    assert all(b["additionalProperties"] is False for b in branches)
    assert all("type" in b["required"] for b in branches)
