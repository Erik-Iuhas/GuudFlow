"""The spec in sessions: co-sign, spec-slice, adherence and after-session routes."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.backlog import Backlog, CardOutcome
from core.harness.task_flow import backlog_text
from core.llm.client import MockLLMClient, MockTurn, ToolCall
from core.project_manager import TOOL_NAME
from core.project_repo import ProjectRepo
from core.spec import Spec
from core.work_db import WorkDB
from tests.spec_fixtures import RULE1, moved, review, seed_spec, write_board

R11 = "R1.1: a saved recipe shows in the list"
R12 = "R1.2: an empty title is refused"
R21 = "R2.1: a tag filters the list"
RL1 = f"rule-1: {RULE1}"

def _text(content: object) -> str:
    """A user message's text (the project manager sends text blocks)."""
    return content if isinstance(content, str) else "".join(
        b["text"] for b in content)  # type: ignore[union-attr]



async def _project(client, auth_header, pid: str = "p1", **doc: Any) -> None:
    r = await client.post("/api/projects", json={"project_id": pid, "goal": "A recipe box",
                                                 **doc}, headers=auth_header)
    assert r.status_code in (200, 201), r.text


def _pending(data_root: Path, run_id: str, title: str, serves: list[str],
             acceptance: list[str]) -> str:
    """A submitted run holding one backlog item; returns the item id."""
    backlog = Backlog(data_root, "p1")
    item = backlog.add(title, acceptance, serves=serves)
    backlog.take_for_run(run_id, ids=[item.id])
    backlog.record_outcomes(run_id, [CardOutcome(title, "done", item.id)])
    repo = ProjectRepo(data_root, "p1")
    base = repo.ensure(None)
    work = data_root / "tmp" / run_id
    repo.clone_into(work)
    (work / f"{run_id}.py").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", f"feat: {run_id}"], cwd=work, check=True)
    repo.submit(run_id, work, base)
    return item.id


def _llm(app, *turns: MockTurn) -> MockLLMClient:
    app.state.meta_llm = MockLLMClient(list(turns))
    return app.state.meta_llm


def _call(reply: str) -> MockTurn:
    return MockTurn(tool_calls=[ToolCall("1", TOOL_NAME, {"reply": reply, "changes": []})])


# ── co-sign ─────────────────────────────────────────────────────────────────
async def test_no_seat_means_no_gate(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    seed_spec(data_root)
    _pending(data_root, "run_a", "Save", ["R1"], [R11])
    rows = (await client.get("/api/projects/p1/runs-review", headers=auth_header)).json()["runs"]
    assert rows[0]["needs_cosign"] is False and rows[0]["cosign"] is None
    r = await client.post("/api/projects/p1/runs-review/run_a/accept", headers=auth_header)
    assert r.status_code == 200


async def test_reviewer_all_gates_accept_until_cosigned(client, auth_header,
                                                        data_root: Path) -> None:
    await _project(client, auth_header, seats={"lead": False, "reviewer": "all"})
    seed_spec(data_root)
    _pending(data_root, "run_a", "Tags", ["R2"], [R21])
    row = (await client.get("/api/projects/p1/runs-review",
                            headers=auth_header)).json()["runs"][0]
    assert row["needs_cosign"] is True and row["cosign"] is None
    blocked = await client.post("/api/projects/p1/runs-review/run_a/accept", headers=auth_header)
    assert blocked.status_code == 409 and blocked.json() == {"detail": "co-sign the code first"}

    r = await client.post("/api/projects/p1/runs-review/run_a/cosign", headers=auth_header,
                          json={"verdict": "cosign", "comments": [
                              {"file": "a.py", "line": 3, "text": "nice", "severity": "note"}]})
    assert r.status_code == 200
    body = r.json()["cosign"]
    assert body["verdict"] == "cosign" and body["by"] == "human" and body["at"] > 0
    assert body["comments"] == [{"file": "a.py", "line": 3, "text": "nice", "severity": "note"}]
    row = (await client.get("/api/projects/p1/runs-review",
                            headers=auth_header)).json()["runs"][0]
    assert row["cosign"]["verdict"] == "cosign" and row["state"] == "pending"
    ok = await client.post("/api/projects/p1/runs-review/run_a/accept", headers=auth_header)
    assert ok.status_code == 200 and ok.json()["run"]["state"] == "accepted"
    # a decided run cannot be co-signed and no longer needs it
    again = await client.post("/api/projects/p1/runs-review/run_a/cosign", headers=auth_header,
                              json={"verdict": "cosign"})
    assert again.status_code == 409


async def test_reviewer_must_only_gates_runs_serving_a_must_requirement(
        client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header, seats={"lead": False, "reviewer": "must"})
    seed_spec(data_root)                      # R1 is must, R2 is should
    _pending(data_root, "run_s", "Tags", ["R2"], [R21])
    _pending(data_root, "run_m", "Save", ["R1"], [R11])
    rows = {r["run_id"]: r for r in (await client.get(
        "/api/projects/p1/runs-review", headers=auth_header)).json()["runs"]}
    assert rows["run_s"]["needs_cosign"] is False and rows["run_m"]["needs_cosign"] is True
    assert (await client.post("/api/projects/p1/runs-review/run_s/accept",
                              headers=auth_header)).status_code == 200
    assert (await client.post("/api/projects/p1/runs-review/run_m/accept",
                              headers=auth_header)).status_code == 409


async def test_changes_is_a_reject_with_a_note_that_reaches_the_next_brief(
        client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header, seats={"lead": False, "reviewer": "all"})
    seed_spec(data_root)
    item_id = _pending(data_root, "run_a", "Save", ["R1"], [R11])
    r = await client.post("/api/projects/p1/runs-review/run_a/cosign", headers=auth_header,
                          json={"verdict": "changes", "comments": [
                              {"file": "recipes.py", "line": 12, "severity": "blocking",
                               "text": "price is a float"},
                              {"text": "rename helper", "severity": "should_fix"}]})
    assert r.status_code == 200, r.text
    assert r.json()["run"]["state"] == "rejected"
    assert r.json()["cosign"]["verdict"] == "changes"
    run = ProjectRepo(data_root, "p1").get("run_a")
    assert run is not None and run.cosign["verdict"] == "changes"       # type: ignore[index]
    assert "[blocking] recipes.py:12: price is a float" in run.note
    assert "[should_fix] rename helper" in run.note
    item = Backlog(data_root, "p1").get(item_id)
    assert item.status == "open"                                        # reopened
    assert "price is a float" in (item.review_note() or "")
    assert "price is a float" in backlog_text([item])                   # in the next brief


async def test_cosign_needs_a_waiting_run(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    r = await client.post("/api/projects/p1/runs-review/run_zz/cosign", headers=auth_header,
                          json={"verdict": "cosign"})
    assert r.status_code == 404
    bad = await client.post("/api/projects/p1/runs-review/run_zz/cosign", headers=auth_header,
                            json={"verdict": "maybe"})
    assert bad.status_code == 422
    assert (await client.post("/api/projects/p1/runs-review/run_zz/cosign",
                              json={"verdict": "cosign"})).status_code == 401


# ── spec-slice ──────────────────────────────────────────────────────────────
async def test_spec_slice_while_the_run_is_live(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    seed_spec(data_root)
    backlog = Backlog(data_root, "p1")
    item = backlog.add("Save", [R11, R12], serves=["R1"])
    backlog.take_for_run("run_l", ids=[item.id])
    root = write_board(data_root, "run_l", [
        review("T1", [(R11, True), (R12, False), (RL1, True)]),
        review("T1", [(R11, True), (R12, True), (RL1, False)]), moved("T1", "in_progress")])
    (root / "backlog_selection.json").write_text(json.dumps([asdict(item)]))

    r = await client.get("/api/runs/run_l/spec-slice", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] == "p1"
    assert [q["id"] for q in body["requirements"]] == ["R1"]            # R2 is not served
    assert body["requirements"][0]["served_by"] == [item.id]
    assert [c["id"] for c in body["requirements"][0]["checks"]] == ["R1.1", "R1.2"]
    assert [d["id"] for d in body["decisions"]] == ["D1"]               # binding or serving R1
    rules = {x["id"]: x for x in body["rules"]}
    assert rules["rule-1"]["verdicts"] == {"met": 1, "unmet": 1}
    assert rules["rule-2"]["verdicts"] == {"met": 0, "unmet": 0}
    assert body["checks"] == {"R1.1": {"state": "met", "run_id": "run_l"},
                              "R1.2": {"state": "met", "run_id": "run_l"}}
    assert (await client.get("/api/runs/ghost/spec-slice", headers=auth_header)).status_code == 404
    assert (await client.get("/api/runs/run_l/spec-slice")).status_code == 401


async def test_spec_slice_shows_stored_states_before_any_review(client, auth_header,
                                                                data_root: Path) -> None:
    await _project(client, auth_header)
    seed_spec(data_root)
    item = Backlog(data_root, "p1").add("Tags", [R21], serves=["R2"])
    Backlog(data_root, "p1").take_for_run("run_n", ids=[item.id])
    write_board(data_root, "run_n", [])
    body = (await client.get("/api/runs/run_n/spec-slice", headers=auth_header)).json()
    assert body["checks"] == {"R2.1": {"state": "unknown", "run_id": None}}
    assert [d["id"] for d in body["decisions"]] == ["D1", "D2"]         # D2 serves R2


# ── adherence ───────────────────────────────────────────────────────────────
async def test_adherence_over_the_last_runs(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    seed_spec(data_root)
    _pending(data_root, "run_a", "Save", ["R1"], [R11])
    write_board(data_root, "run_a", [
        review("T1", [(R11, True), (RL1, False)]), review("T1", [(R11, True), (RL1, True)]),
        moved("T1", "done")])
    _pending(data_root, "run_b", "Tags", ["R2"], [R21])
    write_board(data_root, "run_b", [review("T1", [(RL1, False)]), moved("T1", "needs_attention")])
    r = await client.get("/api/projects/p1/adherence?last=3", headers=auth_header)
    assert r.status_code == 200
    body = r.json()
    assert {x["run_id"]: x for x in body["runs"]} == {
        "run_a": {"run_id": "run_a", "rule_verdicts": {"rule-1": {"met": 1, "unmet": 1}},
                  "unmet": 0, "fixed": 1},
        "run_b": {"run_id": "run_b", "rule_verdicts": {"rule-1": {"met": 0, "unmet": 1}},
                  "unmet": 1, "fixed": 0}}
    assert body["unmet_merged"] == 0
    one = (await client.get("/api/projects/p1/adherence?last=1", headers=auth_header)).json()
    assert len(one["runs"]) == 1
    assert (await client.get("/api/projects/p1/adherence?last=0",
                             headers=auth_header)).status_code == 422
    empty = (await client.get("/api/projects/p1/adherence", headers=auth_header)).json()
    assert len(empty["runs"]) == 2
    assert (await client.get("/api/projects/ghost/adherence",
                             headers=auth_header)).status_code == 404


# ── after-session ───────────────────────────────────────────────────────────
async def test_after_session_builds_a_report_and_asks_the_pm(client, auth_header, app,
                                                             data_root: Path) -> None:
    await _project(client, auth_header, start_mode="spec_first")
    seed_spec(data_root)
    _pending(data_root, "run_a", "Save", ["R1"], [R11, R12])
    write_board(data_root, "run_a", [
        review("T1", [(R11, True), (R12, False), (RL1, True)])])
    llm = _llm(app, _call("Two follow-ups would help."))
    r = await client.post("/api/projects/p1/plan/after-session/run_a", headers=auth_header)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["author"] for m in body["messages"]] == ["pm"] and body["draft"] is None
    system, content = llm.calls[0]["system"], _text(llm.calls[0]["messages"][0].content)
    assert "Phase: after a session" in system
    assert "Report of session run_a" in content
    assert "Summary: run run_a is pending" in content
    assert "Unmet checks:\n- R1.2: an empty title is refused" in content
    assert "Review of T1: R1.2" in content                          # review note
    assert "Leftovers:" in content
    assert (await client.post("/api/projects/p1/plan/after-session/run_zz",
                              headers=auth_header)).status_code == 404
    assert (await client.post("/api/projects/p1/plan/after-session/run_a")).status_code == 401


async def test_first_accepted_prototype_session_derives_the_spec(client, auth_header, app,
                                                                 data_root: Path) -> None:
    await _project(client, auth_header, start_mode="prototype_first")
    _pending(data_root, "run_a", "Thin slice", [], ["it runs end to end"])
    _pending(data_root, "run_b", "More", [], ["more works"])
    llm = _llm(app, _call("first"), _call("second"), _call("third"))
    # not accepted yet: an ordinary after-session
    await client.post("/api/projects/p1/plan/after-session/run_a", headers=auth_header)
    assert "Phase: after a session" in llm.calls[0]["system"]
    await client.post("/api/projects/p1/runs-review/run_a/accept", headers=auth_header)
    r = await client.post("/api/projects/p1/plan/after-session/run_a", headers=auth_header)
    assert r.status_code == 200
    assert "Phase: derive the spec" in llm.calls[1]["system"]
    # a later session is ordinary again
    await client.post("/api/projects/p1/runs-review/run_b/accept", headers=auth_header)
    await client.post("/api/projects/p1/plan/after-session/run_b", headers=auth_header)
    assert "Phase: after a session" in llm.calls[2]["system"]


async def test_after_session_llm_failure_is_502(client, auth_header, app,
                                                data_root: Path) -> None:
    await _project(client, auth_header)
    _pending(data_root, "run_a", "Save", [], ["works"])

    class Exploding:
        async def complete(self, *a: Any, **k: Any):
            raise RuntimeError("provider down")

    app.state.meta_llm = Exploding()
    r = await client.post("/api/projects/p1/plan/after-session/run_a", headers=auth_header)
    assert r.status_code == 502 and "provider down" in r.json()["detail"]


# ── accept writes the verdicts into the spec ────────────────────────────────
async def test_accept_route_sets_check_states(client, auth_header, data_root: Path) -> None:
    await _project(client, auth_header)
    seed_spec(data_root)
    _pending(data_root, "run_a", "Save", ["R1"], [R11, R12])
    write_board(data_root, "run_a", [review("T1", [(R11, True), (R12, False)])])
    assert (await client.post("/api/projects/p1/runs-review/run_a/accept",
                              headers=auth_header)).status_code == 200
    spec = (await client.get("/api/projects/p1/spec", headers=auth_header)).json()
    checks = {c["id"]: (c["state"], c["run_id"]) for c in spec["requirements"][0]["checks"]}
    assert checks == {"R1.1": ("met", "run_a"), "R1.2": ("unmet", "run_a")}
    assert spec["version"] == 3
    assert Spec(WorkDB(data_root / "projects" / "p1").read_spec_doc()).version == 3
