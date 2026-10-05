"""Tests for core.harness.tasks and the task-board additions to results/workspace."""
import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from pydantic import BaseModel, ValidationError

from core.harness.results import AgentResult, ReviewResult, result_json_schema
from core.harness.tasks import (
    AcceptanceCheck,
    ItemVerdict,
    PlanResult,
    ThreadEntry,
    check_plan,
    check_task_result,
    render_thread,
    validate_plan,
)
from core.run_loader import load_persisted_tasks
from core.workspace import Task
from worker.job_runner import serialise_task

ROLES = ["architect", "developer", "tester"]
SUMMARY = "Implemented the feature and wired it up."
EVIDENCE = "pytest tests/test_x.py passes"
DETAIL = "Missing null check on line 12 of the handler."


def task(id: str = "T1", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id,
        "title": "Build the thing",
        "owner_role": "developer",
        "acceptance": ["it works"],
        "size": "S",
        "kind": "code",
    }
    return {**base, **kw}


def plan(*tasks: dict[str, Any]) -> PlanResult:
    return PlanResult.model_validate({"tasks": list(tasks)})


# ── PlanResult schema ──────────────────────────────────────────────────────


def test_plan_valid() -> None:
    p, err = validate_plan({"tasks": [task("T1"), task("T2", depends_on=["T1"])]}, ROLES)
    assert err is None and p is not None and len(p.tasks) == 2


def test_plan_empty_tasks_fails() -> None:
    p, err = validate_plan({"tasks": []}, ROLES)
    assert p is None and err is not None and "tasks" in err


@pytest.mark.parametrize("bad", ["", "3", "task1", "T"])
def test_plan_bad_id_fails(bad: str) -> None:
    p, err = validate_plan({"tasks": [task(bad)]}, ROLES)
    assert p is None and err is not None and "tasks.0.id" in err


def test_plan_missing_acceptance_fails() -> None:
    p, err = validate_plan({"tasks": [task(acceptance=[])]}, ROLES)
    assert p is None and err is not None and "acceptance" in err


def test_plan_blank_acceptance_item_fails() -> None:
    p, err = validate_plan({"tasks": [task(acceptance=["ok", "  "])]}, ROLES)
    assert p is None and err is not None and "non-empty" in err


def test_plan_short_title_and_extra_field_fail() -> None:
    _, err = validate_plan({"tasks": [task(title="abc")]}, ROLES)
    assert err is not None and "title" in err
    _, err = validate_plan({"tasks": [task(extra=1)]}, ROLES)
    assert err is not None and "unexpected field" in err


# ── check_plan rules ───────────────────────────────────────────────────────


def test_unknown_owner_passes_and_fails() -> None:
    assert check_plan(plan(task()), ROLES) == []
    problems = check_plan(plan(task(owner_role="wizard")), ROLES)
    assert len(problems) == 1 and "unknown owner_role 'wizard'" in problems[0]


def test_duplicate_id() -> None:
    assert check_plan(plan(task("T1"), task("T2")), ROLES) == []
    problems = check_plan(plan(task("T1"), task("T1")), ROLES)
    assert problems == ["T1: duplicate task id"]


def test_unknown_dependency() -> None:
    assert check_plan(plan(task("T1"), task("T2", depends_on=["T1"])), ROLES) == []
    problems = check_plan(plan(task("T1", depends_on=["T9"])), ROLES)
    assert len(problems) == 1 and "unknown task id 'T9'" in problems[0]


def test_self_dependency() -> None:
    problems = check_plan(plan(task("T1", depends_on=["T1"])), ROLES)
    assert problems == ["T1: depends on itself"]


def test_cycle_named() -> None:
    ok = plan(task("T1"), task("T2", depends_on=["T1"]), task("T3", depends_on=["T1", "T2"]))
    assert check_plan(ok, ROLES) == []
    cyc = plan(
        task("T1", depends_on=["T3"]),
        task("T2", depends_on=["T1"]),
        task("T3", depends_on=["T2"]),
    )
    problems = check_plan(cyc, ROLES)
    assert len(problems) == 1
    assert problems[0] == "dependency cycle: T1 -> T3 -> T2 -> T1"


def test_large_task_rejected() -> None:
    assert check_plan(plan(task(size="M")), ROLES) == []
    problems = check_plan(plan(task(size="L")), ROLES)
    assert len(problems) == 1 and "split it into S/M tasks" in problems[0]


def test_validate_plan_joins_problems_with_newlines() -> None:
    p, err = validate_plan({"tasks": [task("T1", owner_role="x", size="L")]}, ROLES)
    assert p is None and err is not None
    assert len(err.splitlines()) == 2


# ── AcceptanceCheck / ItemVerdict ──────────────────────────────────────────


def test_evidence_ok_and_too_short() -> None:
    assert AcceptanceCheck(item="a", met=True, evidence=EVIDENCE).met
    assert AcceptanceCheck(item="a", met=False).evidence == ""
    with pytest.raises(ValidationError, match="evidence"):
        AcceptanceCheck(item="a", met=True, evidence="ok")


def test_item_detail_required_when_unmet() -> None:
    assert ItemVerdict(item="a", met=True).met
    assert ItemVerdict(item="a", met=False, detail=DETAIL).detail == DETAIL
    with pytest.raises(ValidationError, match="detail"):
        ItemVerdict(item="a", met=False, detail="nope")


# ── ReviewResult rules ─────────────────────────────────────────────────────


def test_review_pass_all_items_met() -> None:
    r = ReviewResult(verdict="pass", task_id="T1", items=[{"item": "a", "met": True}])
    assert r.task_id == "T1"


def test_review_pass_with_unmet_item_fails() -> None:
    with pytest.raises(ValidationError, match="unmet"):
        ReviewResult(verdict="pass", items=[{"item": "a", "met": False, "detail": DETAIL}])


def test_review_fail_with_unmet_item_only_ok() -> None:
    r = ReviewResult(verdict="fail", items=[{"item": "a", "met": False, "detail": DETAIL}])
    assert r.issues == []


def test_review_fail_without_issues_or_unmet_fails() -> None:
    with pytest.raises(ValidationError, match="issues"):
        ReviewResult(verdict="fail")
    with pytest.raises(ValidationError, match="issues"):
        ReviewResult(verdict="fail", items=[{"item": "a", "met": True}])


# ── check_task_result ──────────────────────────────────────────────────────


def done(task_id: str | None, *checks: dict[str, Any]) -> AgentResult:
    return AgentResult.model_validate(
        {"status": "done", "summary": SUMMARY, "task_id": task_id, "acceptance": list(checks)}
    )


def chk(item: str, met: bool = True) -> dict[str, Any]:
    return {"item": item, "met": met, "evidence": EVIDENCE if met else ""}


def test_task_result_ok_exact_and_prefix() -> None:
    acc = ["The endpoint returns 200", "Docs are updated"]
    assert check_task_result(done("T1", chk(acc[0]), chk(acc[1])), "T1", acc) == []
    assert check_task_result(done("T1", chk("the endpoint returns"), chk("DOCS ARE UPDATED")), "T1", acc) == []


def test_task_result_id_mismatch() -> None:
    problems = check_task_result(done("T2", chk("it works")), "T1", ["it works"])
    assert len(problems) == 1 and "task_id" in problems[0]
    assert check_task_result(done(None, chk("it works")), "T1", ["it works"]) != []


def test_task_result_missing_item() -> None:
    problems = check_task_result(done("T1", chk("first item")), "T1", ["first item", "second item"])
    assert problems == ["acceptance: missing an entry for item 'second item'"]


def test_task_result_unmet_item_in_done() -> None:
    problems = check_task_result(done("T1", chk("first item", met=False)), "T1", ["first item"])
    assert len(problems) == 1 and "unmet" in problems[0]


def test_task_result_not_done_skips_acceptance() -> None:
    blocked = AgentResult.model_validate(
        {
            "status": "failed",
            "summary": SUMMARY,
            "task_id": "T1",
        }
    )
    assert check_task_result(blocked, "T1", ["x item here"]) == []


# ── schemas stay inlined ───────────────────────────────────────────────────


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


@pytest.mark.parametrize("model", [AgentResult, ReviewResult, PlanResult])
def test_schema_has_no_refs(model: type[BaseModel]) -> None:
    schema = result_json_schema(model)
    for node in _walk(schema):
        if isinstance(node, dict):
            assert "$ref" not in node and "$defs" not in node
            if "properties" in node:
                assert node["additionalProperties"] is False


def test_plan_schema_details() -> None:
    schema = result_json_schema(PlanResult)
    item = schema["properties"]["tasks"]["items"]
    assert item["properties"]["size"]["enum"] == ["S", "M", "L"]
    assert item["properties"]["acceptance"]["minItems"] == 1
    assert schema["properties"]["tasks"]["minItems"] == 1


# ── thread ─────────────────────────────────────────────────────────────────


def test_thread_roundtrip_and_frozen() -> None:
    e = ThreadEntry("T1", "note", "dev", "hello", iteration=2, data={"k": 1})
    assert ThreadEntry.from_dict(e.to_dict()) == e
    assert ThreadEntry.from_dict({"task_id": "T1", "kind": "note", "author": "a", "text": "t"}).iteration == 0
    with pytest.raises(AttributeError):
        e.text = "x"  # type: ignore[misc]


def test_render_thread_limits_to_recent() -> None:
    entries = [ThreadEntry("T1", "note", "dev", f"msg {i}") for i in range(5)]
    out = render_thread(entries, limit=2)
    assert out == "- [note] dev: msg 3\n- [note] dev: msg 4"
    assert render_thread([]) == ""
    assert len(render_thread(entries).splitlines()) == 5


# ── Task persistence round-trip ────────────────────────────────────────────


def test_task_new_fields_round_trip(tmp_path: Path) -> None:
    entry = ThreadEntry("T1", "decision", "lead", "use sqlite").to_dict()
    t = Task(
        task_id="T1", story_id="s", assigned_to="developer", goal="g",
        title="Build the thing", kind="test", size="S", files_hint=["a.py"],
        depends_on=["T0"], visual_refs=["mock.png"], column="review",
        review_round=2, thread=[entry],
    )
    (tmp_path / "tasks.jsonl").write_text(json.dumps(serialise_task(t)) + "\n", encoding="utf-8")
    tasks, queues = load_persisted_tasks(tmp_path)
    assert tasks["T1"] == t
    assert queues == {"developer": ["T1"]}


def test_task_old_row_without_new_fields_loads(tmp_path: Path) -> None:
    old = {"task_id": "t1", "story_id": "s", "assigned_to": "dev", "goal": "alpha", "status": "PENDING"}
    (tmp_path / "tasks.jsonl").write_text(json.dumps(old) + "\n", encoding="utf-8")
    tasks, _ = load_persisted_tasks(tmp_path)
    loaded = tasks["t1"]
    assert (loaded.title, loaded.kind, loaded.size, loaded.column) == ("", "code", "M", "todo")
    assert loaded.files_hint == [] and loaded.depends_on == [] and loaded.thread == []
    assert loaded.review_round == 0


def test_plan_must_cover_the_runs_backlog_items() -> None:
    from core.harness.tasks import PlanResult, check_plan
    plan = PlanResult.model_validate({"tasks": [
        {"id": "T1", "title": "Login endpoint", "owner_role": "dev", "acceptance": ["works"],
         "size": "S", "kind": "code", "backlog_id": "B1"},
        {"id": "T2", "title": "Tidy imports", "owner_role": "dev", "acceptance": ["clean"],
         "size": "S", "kind": "code"},
        {"id": "T3", "title": "Stray item work", "owner_role": "dev", "acceptance": ["x"],
         "size": "S", "kind": "code", "backlog_id": "B9"}]})
    problems = check_plan(plan, ["dev"], backlog_ids=["B1", "B2"])
    assert any("B2 has no task" in p for p in problems)
    assert any("T3: backlog_id 'B9'" in p for p in problems)
    assert check_plan(plan, ["dev"]) != []               # B9 is not a run item either
    ok = PlanResult.model_validate({"tasks": [plan.tasks[0].model_dump(),
                                              plan.tasks[1].model_dump()]})
    assert check_plan(ok, ["dev"], backlog_ids=["B1"]) == []
