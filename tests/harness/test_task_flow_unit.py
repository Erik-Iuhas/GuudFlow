"""TaskFlow without opencode: role sets, completion and human board edits (O23, S34)."""

from __future__ import annotations

from core.harness.card_ops import human_add_card, human_comment
from core.harness.task_flow import TaskFlow
from core.harness.tasks import PlannedTask, PlanResult
from core.runner import AgentConfig
from core.signals import SignalType
from core.workspace import Workspace


class FakeScheduler:
    def __init__(self) -> None:
        self.rearmed: list[str] = []
        self.max_iterations = 12

    def is_running(self, role: str) -> bool:
        return False

    def rearm(self, role: str, payload: object = None) -> None:
        self.rearmed.append(role)

    def notify(self) -> None:
        pass


def _flow() -> tuple[TaskFlow, Workspace, FakeScheduler]:
    ws = Workspace(run_id="r", goal="g")
    sched = FakeScheduler()
    agents = [AgentConfig(role="research"), AgentConfig(role="planner", depends_on=["research"]),
              AgentConfig(role="api", depends_on=["planner"]),
              AgentConfig(role="ui", depends_on=["planner", "api"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["api", "ui"])]
    types = {"research": "researcher", "planner": "planner", "api": "builder",
             "ui": "frontend_builder", "reviewer": "reviewer"}
    posted: list[tuple[SignalType, str]] = []
    flow = TaskFlow.build(agents, types, ws, lambda: sched,  # type: ignore[arg-type,return-value]
                          post=lambda k, r, s, d: posted.append((k, s)),
                          escalate=lambda r, s, d: None)
    assert flow is not None
    return flow, ws, sched


def test_roles_downstream_of_the_planner_work_cards() -> None:
    flow, _, _ = _flow()
    assert flow.card_roles == {"api", "ui", "reviewer"}
    assert flow.board.reviewers_of == {"api": "reviewer", "ui": "reviewer"}
    assert flow.depends_on(AgentConfig(role="ui", depends_on=["planner", "api"])) == ["planner"]
    assert flow.depends_on(AgentConfig(role="planner", depends_on=["research"])) == ["research"]
    assert flow.owners() == ["api", "ui"]


def test_no_planner_means_role_mode() -> None:
    ws = Workspace(run_id="r", goal="g")
    assert TaskFlow.build([AgentConfig(role="builder")], {"builder": "builder"}, ws,
                          lambda: None, post=lambda *a: None,  # type: ignore[arg-type,return-value]
                          escalate=lambda *a: None) is None


def test_plan_raises_spawn_cap_and_roles_without_cards_complete() -> None:
    flow, ws, sched = _flow()
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="Client module", owner_role="api", acceptance=["get() works"],
        size="S", kind="code")]))
    assert sched.max_iterations >= 12
    flow.settle()
    assert "ui" in ws.completed_agents                 # nothing to do
    assert "api" not in ws.completed_agents and "reviewer" not in ws.completed_agents


def test_human_card_for_a_finished_role_rearms_it() -> None:
    flow, ws, sched = _flow()
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="Client module", owner_role="api", acceptance=["get() works"],
        size="S", kind="code")]))
    flow.settle()
    assert "ui" in ws.completed_agents
    ws.board_listeners.append(flow.settle)
    card = human_add_card(ws, owner="ui", title="Add a dark mode toggle",
                          acceptance=["toggle switches the theme"])
    assert "ui" in sched.rearmed and flow.has_work("ui")
    human_comment(ws, card.task_id, "Use the existing colour tokens.")
    assert "Use the existing colour tokens." in flow.board.brief_for(card.task_id)


def test_planner_brief_carries_the_measured_card_target() -> None:
    flow, _, _ = _flow()
    flow.size_budget = (10.0, 0.5)
    plan = flow.begin_turn("planner")
    assert plan is not None
    text = dict(plan.extra_sections)["Budget and card size"]
    assert "at most 16 cards" in text and "$0.50" in text and "$10.00" in text
    flow.size_budget = (None, None)
    plan = flow.begin_turn("planner")
    assert plan is not None
    assert "at most 8 cards" in dict(plan.extra_sections)["Budget and card size"]


def test_continues_same_card_dependency_or_shared_files() -> None:
    flow, _, _ = _flow()
    flow.plan_accepted(PlanResult(tasks=[
        PlannedTask(id="T1", title="Status endpoint", owner_role="api", acceptance=["works"],
                    files_hint=["api.py"], size="S", kind="code"),
        PlannedTask(id="T2", title="Status client", owner_role="api", acceptance=["works"],
                    depends_on=["T1"], size="S", kind="code"),
        PlannedTask(id="T3", title="Status docs", owner_role="api", acceptance=["works"],
                    files_hint=["api.py", "README.md"], size="S", kind="code"),
        PlannedTask(id="T4", title="Unrelated page", owner_role="api", acceptance=["works"],
                    files_hint=["page.py"], size="S", kind="code")]))
    assert flow.continues("T1", "T1")
    assert flow.continues("T1", "T2")          # depends on it
    assert flow.continues("T1", "T3")          # shares api.py
    assert not flow.continues("T1", "T4")
    assert flow.continues(None, "T4")          # nothing to compare: role mode
