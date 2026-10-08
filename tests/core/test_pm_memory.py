"""Jack: the seed persona, his prompt layers, recall in the plan turn, and his episodes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agents.persona import Episode
from core import pm_memory, project_runs
from core.llm.client import MockLLMClient, MockTurn, ToolCall
from core.plan_drafts import Draft, DraftChange, PlanDrafts
from core.pm_memory import classify_project, record_draft_outcome, record_session_outcome
from core.pm_persona import PM_PERSONA_ID, SEED_DIR, load_pm, pm_dir
from core.pm_prompts import persona_layers
from core.project_manager import MEMORY_HEADING, TOOL_NAME, ProjectManager
from core.project_repo import ProjectRepo
from tests.spec_fixtures import RULE1, review, seed_spec, write_board

R11 = "R1.1: a saved recipe shows in the list"
R12 = "R1.2: an empty title is refused"
RL1 = f"rule-1: {RULE1}"
GOAL = "A browser game about recipes"
DOC = {"goal": GOAL, "start_mode": "prototype_first",
       "agents": [{"role": "planner"}, {"role": "builder"}, {"role": "reviewer"}],
       "seats": {"lead": False, "reviewer": "off"}}

def _text(content: object) -> str:
    """A user message's text (the project manager sends text blocks)."""
    return content if isinstance(content, str) else "".join(
        b["text"] for b in content)  # type: ignore[union-attr]



def _projects(data_root: Path, doc: dict[str, Any] | None = None) -> None:
    (data_root / "projects.json").write_text(json.dumps({"p1": doc or DOC}), encoding="utf-8")


def _episodes(data_root: Path) -> list[dict[str, Any]]:
    path = pm_dir(data_root) / "episodes.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _submit(data_root: Path, run_id: str) -> None:
    repo = ProjectRepo(data_root, "p1")
    base = repo.ensure(None)
    work = data_root / "tmp" / run_id
    repo.clone_into(work)
    (work / f"{run_id}.py").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: a"], cwd=work, check=True)
    repo.submit(run_id, work, base)


def _draft(*changes: tuple[str, bool | None, str]) -> Draft:
    return Draft(id="d1", base_version=3, changes=[
        DraftChange(cid=f"c{i}", change={"op": op}, accepted=acc, by=by)
        for i, (op, acc, by) in enumerate(changes, 1)])


# ── seed and loading ────────────────────────────────────────────────────────
def test_seed_has_all_six_files_and_copies_without_overwriting(tmp_path: Path) -> None:
    from scripts.seed_data import _seed_personas
    names = {p.name for p in SEED_DIR.iterdir()}
    assert names == {"profile.json", "episodes.jsonl", "patterns.json", "style.json",
                     "reputation.json", "history.json"}
    seed_root = SEED_DIR.parent.parent
    assert _seed_personas(seed_root, tmp_path) >= 1
    target = tmp_path / "personas" / PM_PERSONA_ID
    assert (target / "profile.json").is_file()
    (target / "profile.json").write_text('{"persona_id": "x", "name": "Renamed"}')
    _seed_personas(seed_root, tmp_path)
    assert "Renamed" in (target / "profile.json").read_text()


def test_load_pm_creates_jack(tmp_path: Path) -> None:
    jack = load_pm(tmp_path)
    assert (jack.profile.persona_id, jack.profile.name) == (PM_PERSONA_ID, "Jack")
    assert jack.profile.specialty == "project manager" and not jack.has_memory()
    assert (pm_dir(tmp_path) / "profile.json").is_file()
    again = load_pm(tmp_path)   # does not recreate or reset
    assert again.profile.name == "Jack"


def test_renamed_jack_keeps_his_new_name(tmp_path: Path) -> None:
    load_pm(tmp_path)
    path = pm_dir(tmp_path) / "profile.json"
    doc = json.loads(path.read_text())
    doc["name"] = "Pat"
    path.write_text(json.dumps(doc))
    assert load_pm(tmp_path).profile.name == "Pat"
    assert "You are Pat" in persona_layers(load_pm(tmp_path))


def test_persona_layers_have_identity_character_and_stake(tmp_path: Path) -> None:
    jack = load_pm(tmp_path)
    text = persona_layers(jack)
    assert text.startswith("You are Jack, a project manager.")
    assert jack.profile.character in text and jack.profile.stake_framing in text


def test_classifier() -> None:
    assert classify_project("A web game") == "game"
    assert classify_project("a command-line tool") == "cli"
    assert classify_project("REST API for recipes") == "api"
    assert classify_project("A recipe website") == "web"
    assert classify_project("Python library for dates") == "library"
    assert classify_project("ETL pipeline for sales data") == "data"
    assert classify_project("user guide") == "docs"
    assert classify_project("something vague") == "other"


# ── the PM turn: identity and recall ────────────────────────────────────────
def _turn(reply: str = "ok") -> MockTurn:
    return MockTurn(tool_calls=[ToolCall("1", TOOL_NAME, {"reply": reply, "changes": []})])


async def test_prompt_has_jack_and_memory_only_with_memory(tmp_path: Path) -> None:
    llm = MockLLMClient([_turn(), _turn()])
    manager = ProjectManager(llm, tmp_path, "p1", DOC)
    await manager.turn("Plan the browser game", mode="discuss")
    system = llm.calls[0]["system"]
    assert system.startswith("You are Jack, a project manager.")
    assert MEMORY_HEADING not in _text(llm.calls[0]["messages"][0].content)

    _projects(tmp_path)
    record_session_outcome(tmp_path, "p1", "run_x", True, "great")
    await manager.turn("Plan the browser game again", mode="discuss")
    context = _text(llm.calls[1]["messages"][0].content)
    assert MEMORY_HEADING in context and "win" in context
    assert context.index("Current spec") < context.index(MEMORY_HEADING) < context.index(
        "Open draft")


# ── session episodes ────────────────────────────────────────────────────────
def test_accept_records_an_episode_with_facts(tmp_path: Path) -> None:
    _projects(tmp_path)
    seed_spec(tmp_path)
    _submit(tmp_path, "run_a")
    write_board(tmp_path, "run_a", [
        review("T1", [(R11, False), (R12, True), (RL1, False)]),
        review("T1", [(R11, True), (R12, True), (RL1, True)]),
        review("T2", [(R12, False)])])
    project_runs.decide_run(tmp_path, "p1", "run_a", accept=True, note="Looks right to me")

    (ep,) = _episodes(tmp_path)
    assert ep["run_id"] == "run_a" and ep["project_type"] == "game"
    assert ep["feedback_verbatim"] == "Looks right to me"
    assert ep["task_context"] == GOAL and ep["iterations_to_resolve"] == 3
    assert ep["tags"] == ["session", "prototype_first", "game", "accepted", "kind:software_app",
                          "team:builder+planner+reviewer"]
    assert "prototype_first mode" in ep["situation"] and "2 requirements" in ep["situation"]
    assert "3 review round(s)" in ep["what_i_tried"] and "cost unknown" in ep["what_i_tried"]
    assert "session run_a was accepted" in ep["what_worked"]
    assert "checks met: R1.1" in ep["what_worked"]
    assert "1 rule issue(s) fixed in review" in ep["what_worked"]
    assert "checks unmet: R1.2" in ep["what_failed"]

    rep = pm_memory.summary(tmp_path)
    assert rep["episodes"] == 1 and rep["reputation"]["sessions_accepted"] == 1
    assert load_pm(tmp_path).reputation.production.completed_runs == 1


def test_reject_records_the_note_and_the_reputation(tmp_path: Path) -> None:
    _projects(tmp_path)
    seed_spec(tmp_path)
    _submit(tmp_path, "run_b")
    project_runs.decide_run(tmp_path, "p1", "run_b", accept=False, note="Too slow")
    (ep,) = _episodes(tmp_path)
    assert "rejected" in ep["tags"] and ep["feedback_verbatim"] == "Too slow"
    assert "session run_b was rejected" in ep["what_failed"] and ep["what_worked"] == ""
    rep = pm_memory.summary(tmp_path)["reputation"]
    assert (rep["sessions_accepted"], rep["sessions_rejected"]) == (0, 1)
    assert load_pm(tmp_path).reputation.production.failed_runs == 1


def test_decide_run_survives_a_memory_failure(tmp_path: Path, monkeypatch) -> None:
    _projects(tmp_path)
    _submit(tmp_path, "run_c")

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(pm_memory, "record_session_outcome", boom)
    decision = project_runs.decide_run(tmp_path, "p1", "run_c", accept=True)
    assert decision.run.state == "accepted"


def test_record_session_never_raises(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pm_memory, "load_pm", lambda *_: (_ for _ in ()).throw(OSError("x")))
    assert record_session_outcome(tmp_path, "p1", "run_z", True, "") is None


# ── draft episodes ──────────────────────────────────────────────────────────
def test_approved_draft_episode_and_untouched_reputation(tmp_path: Path) -> None:
    _projects(tmp_path)
    ep = record_draft_outcome(tmp_path, "p1", _draft(
        ("add_requirement", True, "pm"), ("add_item", True, "pm"),
        ("add_rule", False, "pm")), approved=True)
    assert isinstance(ep, Episode) and ep.run_id == "d1"
    assert ep.tags == ["draft", "prototype_first", "game", "approved", "kind:software_app",
                       "team:builder+planner+reviewer"]
    assert "proposed 3 change(s): c1:add_requirement, c2:add_item, c3:add_rule" in ep.situation
    assert ep.what_worked == "2 change(s) accepted: c1:add_requirement, c2:add_item"
    assert ep.what_failed == "1 change(s) not accepted: c3:add_rule"
    rep = pm_memory.summary(tmp_path)["reputation"]
    assert rep["drafts_approved"] == 1 and rep["drafts_approved_untouched"] == 0

    record_draft_outcome(tmp_path, "p1", _draft(("add_item", True, "pm")), approved=True)
    assert pm_memory.summary(tmp_path)["reputation"]["drafts_approved_untouched"] == 1


def test_draft_edited_by_the_human_is_tagged(tmp_path: Path) -> None:
    _projects(tmp_path)
    ep = record_draft_outcome(tmp_path, "p1", _draft(
        ("add_requirement", True, "pm"), ("edit_requirement", True, "human")), approved=True)
    assert ep is not None and "human_edited" in ep.tags
    assert pm_memory.summary(tmp_path)["reputation"]["drafts_approved_untouched"] == 0


def test_discarded_draft_counts_every_change_as_rejected(tmp_path: Path) -> None:
    _projects(tmp_path)
    ep = record_draft_outcome(tmp_path, "p1", _draft(
        ("add_requirement", True, "pm"), ("add_item", None, "pm")), approved=False)
    assert ep is not None and "discarded" in ep.tags and ep.what_worked == ""
    assert "2 change(s) not accepted" in ep.what_failed
    assert pm_memory.summary(tmp_path)["reputation"]["drafts_discarded"] == 1


def test_a_draft_without_pm_changes_is_skipped(tmp_path: Path) -> None:
    assert record_draft_outcome(tmp_path, "p1", _draft(("edit_rule", True, "human")),
                                approved=True) is None
    assert not (pm_dir(tmp_path) / "episodes.jsonl").exists()


# ── distillation ────────────────────────────────────────────────────────────
async def test_distill_does_nothing_without_a_cluster(tmp_path: Path) -> None:
    _projects(tmp_path)
    record_session_outcome(tmp_path, "p1", "r1", True, "")

    def no_llm() -> Any:
        raise AssertionError("no client may be built without a cluster")

    assert await pm_memory.distill(tmp_path, no_llm) == 0


async def test_distill_makes_a_pattern_from_three_episodes(tmp_path: Path) -> None:
    _projects(tmp_path)
    for i in range(3):
        record_session_outcome(tmp_path, "p1", f"r{i}", True, "")
    llm = MockLLMClient([MockTurn(tool_calls=[ToolCall("1", "emit_pattern", {
        "title": "Add a renderer builder in session 1",
        "lesson": "Browser games accepted in prototype-first all had a renderer builder early.",
        "applies_when": ["game", "prototype_first"]})])])
    assert await pm_memory.distill(tmp_path, lambda: llm) == 1
    jack = load_pm(tmp_path)
    assert len(jack.patterns) == 1 and jack.patterns[0].source_episodes
    assert len(jack.episodes) == 3
