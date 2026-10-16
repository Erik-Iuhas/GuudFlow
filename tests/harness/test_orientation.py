"""Orientation and the spec within reach (worker context plan E, G)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.harness.brief import BriefInputs, build_brief
from core.harness.handoff import HostFacts
from core.harness.opencode_runner import orientation_calls
from core.harness.run import OpencodeRun
from core.harness.spec_brief import SpecBrief
from core.harness.tasks import PlannedTask, PlanResult
from core.spec import Spec
from tests.harness.test_spec_brief import _flow, _sections


def test_orientation_calls_count_until_the_first_useful_call() -> None:
    calls = [("glob", "completed"), ("grep", "completed"), ("read", "completed"),
             ("edit", "completed"), ("bash", "completed")]
    assert orientation_calls(calls, reviewer=False) == 3
    assert orientation_calls(calls, reviewer=True) == 4
    assert orientation_calls([("read", "completed")] * 6, reviewer=False) == 6


def test_small_models_get_numbered_steps() -> None:
    small = build_brief(BriefInputs(goal="g", role="dev", description="d", small=True))
    assert "1. Read \"Start here\"" in small and "5. Finish with `guudflow_submit_result`" in small
    big = build_brief(BriefInputs(goal="g", role="dev", description="d"))
    assert "1. Read" not in big


def test_cards_start_from_their_files_and_their_dependencies(tmp_path: Path) -> None:
    flow, _ = _flow(SpecBrief(Spec({"stack": {"test_command": "python -m pytest -q"}})), [])
    flow.plan_accepted(PlanResult(tasks=[
        PlannedTask(id="T1", title="Score model", owner_role="api", size="S", kind="code",
                    acceptance=["score starts at 0"], files_hint=["game/score.py"]),
        PlannedTask(id="T2", title="Score HUD", owner_role="api", size="S", kind="code",
                    acceptance=["HUD shows score"], files_hint=["game/hud.py"],
                    depends_on=["T1"])]))
    flow.board.note("T1", "handoff", "api", "handoff", {"facts": HostFacts(
        files=(("game/score.py", 20, 0),), exports=("game/score.py: Score()",)).to_dict()})
    flow.board.card("T1").column = "done"
    t2 = flow.board.card("T2")
    text = dict(flow._start_here(t2))["Start here"]
    assert "`game/hud.py`" in text and "T1 (Score model) changed: `game/score.py`" in text
    assert "Score()" in text and "`python -m pytest -q`" in text


def test_reviewers_get_the_change_in_hand() -> None:
    flow, _ = _flow(None, [])
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="Score", owner_role="api", size="M", kind="code",
        acceptance=["score starts at 0"])]))
    flow.board.record_merge("T1", "api", "aaa", "bbb")
    flow.facts_for = lambda base, head: HostFacts(files=(("game/score.py", 12, 1),))
    flow.diff_for = lambda ranges: "diff --git a/game/score.py b/game/score.py\n+x\n"
    text = dict(flow._change_in_hand(flow.board.card("T1")))["The change to review"]
    assert "`game/score.py` (+12/−1)" in text and "diff --git" in text
    flow.diff_for = lambda ranges: "+x\n" * 400
    text = dict(flow._change_in_hand(flow.board.card("T1")))["The change to review"]
    assert "call `guudflow_changes`" in text


def test_spec_lookup_by_id_and_phrase() -> None:
    spec = SpecBrief(Spec({
        "requirements": [{"id": "R1", "text": "The score resets when a level starts",
                          "kind": "feature", "priority": "must",
                          "checks": [{"id": "R1.1", "text": "score is 0 at level start"}]}],
        "decisions": [{"id": "D1", "title": "Engine", "choice": "pygame", "reason": "the owner "
                       "said so", "binding": True}],
        "stack": {"language": "Python 3.12"}}))
    fake = SimpleNamespace(_spec_brief=lambda: spec, _card_rows=lambda: [
        {"id": "T4", "title": "Reset score", "owner": "api", "column": "todo", "serves": ["R1.1"],
         "acceptance": ["R1.1: score is 0 at level start"]}])
    look = lambda q: OpencodeRun._spec_lookup(fake, q)  # type: ignore[arg-type]  # noqa: E731
    assert look("R1.1").startswith("R1.1 (unknown, unit) of R1")
    assert "pygame" in look("D1") and "Reset score" in look("t4")
    assert "R1" in look("score resets") and "Nothing matches" in look("joystick")
    assert "Python 3.12" in look("stack")
