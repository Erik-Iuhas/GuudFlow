"""Session budget warning, limits in briefs, withdrawn budget guidance (plan §11.4, W7)."""

from __future__ import annotations

from types import SimpleNamespace

from core.harness.detectors import Stall
from core.harness.overseer import OverseerResult
from core.harness.overseer_flow import OverseerFlow
from core.harness.run import HarnessSettings, OpencodeRun
from core.signals import Signal, SignalType
from core.workspace import Workspace


class FakeGateway:
    def __init__(self, spent: float) -> None:
        self.spent = spent

    def spend(self, run_id: str, role: str | None = None) -> float:
        return self.spent


def _run(spent: float, budget: float) -> SimpleNamespace:
    ws = Workspace(run_id="r", goal="g")
    return SimpleNamespace(
        workspace=ws, run_id="r", gateway=FakeGateway(spent), settings=HarnessSettings(),
        runner=SimpleNamespace(project=SimpleNamespace(approval_gates={"token_budget_usd": budget})),
        _budget_warned=None, _limits_seen={})


def test_budget_warning_once_per_budget() -> None:
    run = _run(7.0, 10.0)
    OpencodeRun._budget_watch(run)                       # 70%: quiet
    assert not [s for s in run.workspace.board if s.type is SignalType.BUDGET_WARNING]
    run.gateway.spent = 8.4
    OpencodeRun._budget_watch(run)
    OpencodeRun._budget_watch(run)
    warns = [s for s in run.workspace.board if s.type is SignalType.BUDGET_WARNING]
    assert len(warns) == 1 and "$8.40 of the $10.00" in warns[0].summary
    assert warns[0].priority == "high"
    run.runner.project.approval_gates["token_budget_usd"] = 15.0   # raised: warn again later
    run.gateway.spent = 12.5
    OpencodeRun._budget_watch(run)
    assert len([s for s in run.workspace.board if s.type is SignalType.BUDGET_WARNING]) == 2


def test_each_role_hears_about_a_limits_change_once() -> None:
    run = _run(9.3, 15.0)
    assert OpencodeRun._limits_section(run, "dev") == ()
    run.workspace.post_signal(Signal(type=SignalType.RUN_LIMITS_CHANGED,
                                     summary="run limits changed: budget_usd=15.0"))
    (title, body), = OpencodeRun._limits_section(run, "dev")
    assert title == "Session limits changed"
    assert "budget_usd=15.0" in body and "$9.30 spent" in body and "no longer applies" in body
    assert OpencodeRun._limits_section(run, "dev") == ()           # once
    assert OpencodeRun._limits_section(run, "rev") != ()           # every role


def test_raising_the_budget_withdraws_budget_only_guidance() -> None:
    flow = OverseerFlow.__new__(OverseerFlow)
    flow.addenda, flow._addenda_from, flow._burn_woken = {}, {}, (5.0, 10.0)
    flow.addenda["dev"] = ["Budget nearly spent: finish fast", "T3 loops on the same test: split it"]
    flow._addenda_from["dev"] = [frozenset({"burn_without_output"}),
                                 frozenset({"same_review_issue"})]
    flow.addenda["rev"] = ["Budget nearly spent: review quickly"]
    flow._addenda_from["rev"] = [frozenset({"burn_without_output"})]
    assert flow.withdraw_budget_guidance() == 2
    assert flow.addenda == {"dev": ["T3 loops on the same test: split it"]}
    assert flow._burn_woken is None


def test_overseer_text_with_html_entities_is_stored_plainly() -> None:
    r = OverseerResult.model_validate({
        "assessment": "Nothing is stuck. Priority order: T4 &gt; T6 &gt; T5.",
        "interventions": [{"type": "decide", "question": "What order &amp; why?",
                           "decision": "Build order T4 &gt; T6", "rationale": "the renderer needs T4 &lt; T7"}]})
    assert "T4 > T6 > T5" in r.assessment
    dumped = r.model_dump_json()
    assert "&gt;" not in dumped and "&amp;" not in dumped


def test_overseer_wake_is_announced_on_the_board() -> None:
    from core.harness.detectors import RunView
    from tests.harness.test_overseer_flow import _setup
    flow, _board, _ws, posted = _setup()
    flow.watch.pending = [Stall("burn_without_output", "", "spent $5.10 with 3/8 cards done")]
    flow.begin_turn(RunView(signals=[], cards=[], role_states={}, running=[], spent_usd=5.1,
                            budget_usd=10.0), {})
    wake = [summary for kind, summary in posted if kind is SignalType.OVERSEER_WAKE]
    assert wake == ["overseer woken: burn_without_output"]
