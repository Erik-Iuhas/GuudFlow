"""Tests for core.harness.results."""
import json
from typing import Any, Iterator

import pytest
from pydantic import BaseModel, ValidationError

from core.harness.results import (
    AgentResult,
    ReviewResult,
    build_retry_prompt,
    result_json_schema,
    validate_result,
)

SUMMARY = "Implemented the feature and wired it up."
NEED = "Please publish the API contract first."
DETAIL = "Missing null check on line 12 of the handler."


def ok(**kw: Any) -> dict[str, Any]:
    return {"status": "done", "summary": SUMMARY, **kw}


@pytest.mark.parametrize("status", ["done", "failed"])
def test_agent_valid_non_blocked(status: str) -> None:
    assert AgentResult(status=status, summary=SUMMARY).files_changed == []


def test_agent_valid_blocked() -> None:
    r = AgentResult(status="blocked", summary=SUMMARY, blocked_on={"role": "api", "need": NEED})
    assert r.blocked_on is not None and r.blocked_on.role == "api"


def test_agent_blocked_without_blocked_on_fails() -> None:
    with pytest.raises(ValidationError, match="required when status is 'blocked'"):
        AgentResult(status="blocked", summary=SUMMARY)


def test_agent_done_with_blocked_on_fails() -> None:
    with pytest.raises(ValidationError, match="must be null"):
        AgentResult(**ok(blocked_on={"role": "api", "need": NEED}))


def test_agent_short_need_fails() -> None:
    with pytest.raises(ValidationError):
        AgentResult(status="blocked", summary=SUMMARY, blocked_on={"role": "api", "need": "help"})


def test_agent_short_summary_fails() -> None:
    with pytest.raises(ValidationError):
        AgentResult(status="done", summary="too short")


def test_agent_extra_field_fails() -> None:
    with pytest.raises(ValidationError):
        AgentResult(**ok(bogus=1))


def test_agent_bad_status_fails() -> None:
    with pytest.raises(ValidationError):
        AgentResult(**{**ok(), "status": "maybe"})


def test_agent_notes_valid() -> None:
    r = AgentResult(**ok(notes_for={"qa": "Check the edge case."}))
    assert r.notes_for == {"qa": "Check the edge case."}


@pytest.mark.parametrize("notes", [{"": "note"}, {"qa": ""}, {"qa": "  "}])
def test_agent_empty_notes_fail(notes: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="notes_for"):
        AgentResult(**ok(notes_for=notes))


def test_review_pass_without_issues() -> None:
    assert ReviewResult(verdict="pass").issues == []


def test_review_fail_with_issue() -> None:
    r = ReviewResult(verdict="fail", issues=[{"file": "a.py", "detail": DETAIL}])
    assert len(r.issues) == 1


def test_review_fail_without_issues_fails() -> None:
    with pytest.raises(ValidationError, match="issues"):
        ReviewResult(verdict="fail")


def test_review_short_detail_fails() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(verdict="fail", issues=[{"file": "a.py", "detail": "bad"}])


def test_review_empty_file_fails() -> None:
    with pytest.raises(ValidationError):
        ReviewResult(verdict="fail", issues=[{"file": "", "detail": DETAIL}])


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


@pytest.mark.parametrize("model", [AgentResult, ReviewResult])
def test_schema_inlined_and_closed(model: type[BaseModel]) -> None:
    schema = result_json_schema(model)
    for node in _walk(schema):
        if isinstance(node, dict):
            assert "$ref" not in node and "$defs" not in node
            assert "title" not in node
            if node.get("type") == "object":
                # Model objects are closed; maps keep their value schema.
                if "properties" in node:
                    assert node["additionalProperties"] is False
                else:
                    assert isinstance(node["additionalProperties"], dict)


def test_agent_schema_details() -> None:
    schema = result_json_schema(AgentResult)
    assert schema["required"] == ["status", "summary"]
    assert schema["properties"]["status"]["enum"] == ["done", "blocked", "failed"]
    any_of = schema["properties"]["blocked_on"]["anyOf"]
    assert {"type": "null"} in any_of
    inlined = next(x for x in any_of if x.get("type") == "object")
    assert inlined["required"] == ["role", "need"]
    assert inlined["additionalProperties"] is False


def test_review_schema_details() -> None:
    schema = result_json_schema(ReviewResult)
    assert schema["required"] == ["verdict"]
    assert schema["properties"]["verdict"]["enum"] == ["pass", "fail"]
    item = schema["properties"]["issues"]["items"]
    assert item["required"] == ["file", "detail"]
    assert item["additionalProperties"] is False


def test_validate_dict_ok() -> None:
    r, err = validate_result(AgentResult, ok())
    assert err is None and r is not None and r.status == "done"


def test_validate_json_string_ok() -> None:
    r, err = validate_result(ReviewResult, json.dumps({"verdict": "pass"}))
    assert err is None and r is not None


def test_validate_none() -> None:
    assert validate_result(AgentResult, None) == (None, "no structured result was produced")


def test_validate_bad_json_string() -> None:
    r, err = validate_result(AgentResult, "{nope")
    assert r is None and err is not None and "JSON" in err


@pytest.mark.parametrize("raw", [[1, 2], 5, "[1]"])
def test_validate_wrong_type(raw: Any) -> None:
    r, err = validate_result(AgentResult, raw)
    assert r is None and err is not None and "object" in err


def test_validate_invalid_dict_names_field() -> None:
    r, err = validate_result(AgentResult, {"status": "done", "summary": "short"})
    assert r is None and err is not None and err.startswith("summary:")


def test_validate_cross_field_message() -> None:
    _, err = validate_result(AgentResult, {"status": "blocked", "summary": SUMMARY})
    assert err == "blocked_on: required when status is 'blocked'"


def test_validate_extra_field_names_field() -> None:
    _, err = validate_result(AgentResult, ok(bogus=1))
    assert err is not None and "bogus" in err


def test_validate_multiple_problems_one_line_each() -> None:
    _, err = validate_result(AgentResult, {"status": "x", "summary": "s"})
    assert err is not None and len(err.splitlines()) == 2


def test_retry_prompt() -> None:
    p = build_retry_prompt(AgentResult, "summary: too short")
    assert "summary: too short" in p and "StructuredOutput" in p


def test_schema_keeps_map_value_schema_for_notes_for() -> None:
    """dict[str, str] must stay a map, not be closed to no keys at all."""
    from core.harness.results import AgentResult, result_json_schema

    notes = result_json_schema(AgentResult)["properties"]["notes_for"]
    assert notes["type"] == "object"
    assert notes["additionalProperties"] == {"type": "string"}
