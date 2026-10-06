"""The Studio event contract matches what the backend really emits (O21)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.harness.event_bridge import EventBridge
from core.harness.event_contract import DETAIL_SHAPES, known_fields, required_fields
from core.harness.overseer import OverseerResult, intervention_signal
from core.harness.results import AcceptanceCheck, AgentResult, ItemVerdict, ReviewResult
from core.harness.task_flow import TaskFlow
from core.harness.tasks import PlannedTask, PlanResult
from core.runner import AgentConfig
from core.signals import Signal, SignalType
from core.workspace import Workspace
from tests.harness.test_task_flow_unit import FakeScheduler

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "events_builder_turn.jsonl"


def _check(sig: Signal) -> None:
    kind = sig.type.value
    if kind not in DETAIL_SHAPES:
        return
    detail = json.loads(sig.detail or "{}")
    missing = required_fields(kind) - set(detail)
    unknown = set(detail) - known_fields(kind)
    assert not missing, f"{kind} is missing {missing}: {detail}"
    assert not unknown, f"{kind} has fields the contract doesn't know: {unknown}"


def test_task_flow_signals_match_the_contract() -> None:
    ws = Workspace(run_id="r", goal="g")
    signals: list[Signal] = []
    ws.subscribe(signals.append)
    agents = [AgentConfig(role="planner"), AgentConfig(role="api", depends_on=["planner"]),
              AgentConfig(role="ui", depends_on=["planner"]),
              AgentConfig(role="rev", is_reviewer=True, depends_on=["api"])]

    def post(kind: SignalType, role: str, summary: str, detail: str | None) -> None:
        ws.post_signal(Signal(type=kind, summary=summary, from_agent=role, detail=detail))

    flow = TaskFlow.build(agents, {"planner": "planner", "api": "builder", "ui": "builder",
                                   "rev": "reviewer"}, ws, lambda: FakeScheduler(),  # type: ignore[arg-type,return-value]
                          post=post, escalate=lambda *a: None)
    assert flow is not None
    flow.plan_accepted(PlanResult(tasks=[
        PlannedTask(id="T1", title="Client", owner_role="api", acceptance=["get works"],
                    size="S", kind="code"),
        PlannedTask(id="T2", title="Status page", owner_role="ui", acceptance=["page renders"],
                    size="S", kind="code")]))
    flow.begin_turn("api")
    flow.apply_result("api", "T1", AgentResult(
        status="done", task_id="T1", summary="Built the client module.",
        acceptance=[AcceptanceCheck(item="get works", met=True, evidence="test passes")]),
        "c1", ("a" * 40, "b" * 40))
    flow.end_turn("api")
    flow.begin_turn("rev")
    flow.apply_result("rev", "T1", ReviewResult(verdict="fail", task_id="T1", items=[
        ItemVerdict(item="get works", met=False, detail="get() ignores the timeout argument")]), "")
    flow.begin_turn("ui")
    flow.apply_result("ui", "T2", AgentResult.model_validate({
        "status": "blocked", "task_id": "T2", "summary": "Waiting for the client API.",
        "blocked_on": {"role": "api", "need": "Expose fetch_status() in the client please."}}), "c")
    kinds = {s.type for s in signals}
    assert {SignalType.TASK_MOVED, SignalType.TASK_THREAD, SignalType.REJECTED,
            SignalType.BLOCKED} <= kinds
    for sig in signals:
        _check(sig)


def test_overseer_and_bridge_signals_match_the_contract() -> None:
    result = OverseerResult.model_validate({
        "assessment": "The builder is stuck; one more round will fix it.",
        "interventions": [
            {"type": "rebrief", "role": "api", "addendum": "Handle the timeout argument.",
             "rationale": "same issue twice in a row"},
            {"type": "split", "task_id": "T1", "rationale": "the card is far too big",
             "parts": [{"title": "Part one", "acceptance": ["a"]},
                       {"title": "Part two", "acceptance": ["b"]}]}]})
    for iv in result.interventions:
        _check(intervention_signal(iv))
    emitted: list[Signal] = []
    bridge = EventBridge("builder", emitted.append)
    for line in FIXTURE.read_text().splitlines():
        bridge.handle(json.loads(line))
    assert any(s.type is SignalType.TOOL_CALL for s in emitted)
    for sig in emitted:
        _check(sig)


def test_generated_typescript_is_up_to_date() -> None:
    proc = subprocess.run([sys.executable, "scripts/gen_studio_events.py", "--check"], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout
