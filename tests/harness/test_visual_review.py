"""O24: the visual review loop (plan §5.10) — pre-check, staging, baselines, caps,
human annotations — driven through TaskFlow without opencode."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image

from core.harness.mcp_server import McpIdentity, McpTool, ToolResult
from core.harness.results import AcceptanceCheck, AgentResult, ItemVerdict, ReviewResult
from core.harness.task_flow import TaskFlow
from core.harness.tasks import PlannedTask, PlanResult
from core.harness.visual_review import (
    ImageBudget,
    capped,
    latest_screenshot,
    precheck,
    resolve_ref,
)
from core.runner import AgentConfig
from core.signals import Signal, SignalType
from core.workspace import Workspace
from tests.harness.test_task_flow_unit import FakeScheduler

ITEM = "the header matches the mockup"


def _png(path: Path, color: tuple[int, int, int], box: tuple[int, int, int, int] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (100, 60), color)
    if box:
        img.paste((0, 0, 0), box)
    img.save(path)
    return path


def _setup(tmp_path: Path) -> tuple[TaskFlow, Workspace]:
    ws = Workspace(run_id="r", goal="g")
    agents = [AgentConfig(role="planner"), AgentConfig(role="ui", depends_on=["planner"]),
              AgentConfig(role="reviewer", is_reviewer=True, depends_on=["ui"])]
    flow = TaskFlow.build(agents, {"planner": "planner", "ui": "frontend_builder",
                                   "reviewer": "reviewer"}, ws, lambda: FakeScheduler(),  # type: ignore[arg-type,return-value]
                          post=lambda *a: None, escalate=lambda *a: None)
    assert flow is not None
    flow.tool_dir_for = lambda role: tmp_path / "tools" / role
    flow.ref_roots = [tmp_path / "inputs"]
    flow.run_root = tmp_path / "run"
    flow.vision_for = lambda role: role == "reviewer"
    _png(tmp_path / "inputs" / "mockups" / "home.png", (255, 255, 255))
    flow.plan_accepted(PlanResult(tasks=[PlannedTask(
        id="T1", title="Home page header", owner_role="ui", acceptance=[ITEM], size="S",
        kind="visual", visual_refs=["inputs/mockups/home.png"])]))
    return flow, ws


def _done() -> AgentResult:
    return AgentResult(status="done", task_id="T1", summary="Built the header to match the mockup.",
                       acceptance=[AcceptanceCheck(item=ITEM, met=True, evidence="screenshot compared")])


def test_helpers(tmp_path: Path) -> None:
    assert latest_screenshot(tmp_path) is None
    old = _png(tmp_path / "screens" / "a.png", (0, 0, 0))
    new = _png(tmp_path / "screens" / "b.png", (0, 0, 0))
    import os
    os.utime(old, (1, 1))
    assert latest_screenshot(tmp_path) == new
    assert resolve_ref("inputs/../etc/passwd", [tmp_path]) is None
    assert resolve_ref("inputs/screens/a.png", [tmp_path]) == old  # strips "inputs/"


def test_precheck_verdicts(tmp_path: Path) -> None:
    flow, _ = _setup(tmp_path)
    card = flow.board.card("T1")
    tools = tmp_path / "tools" / "ui"
    out = tmp_path / "out"
    assert not precheck(card, tools, flow.ref_roots, out).passed           # no screenshot
    _png(tools / "screens" / "1.png", (0, 0, 0))
    bad = precheck(card, tools, flow.ref_roots, out)
    assert not bad.passed and bad.diff_image is not None and "100%" in bad.detail
    _png(tools / "screens" / "2.png", (255, 255, 255), box=(0, 0, 10, 10))
    good = precheck(card, tools, flow.ref_roots, out)
    assert good.passed and 0 < good.fractions["inputs/mockups/home.png"] < 0.25


def test_visual_card_loop_through_taskflow(tmp_path: Path) -> None:
    flow, ws = _setup(tmp_path)
    tools = tmp_path / "tools" / "ui"
    plan = flow.begin_turn("ui")
    assert plan is not None and "guudflow_screenshot" in (plan.task_text or "")
    _png(tools / "screens" / "1.png", (0, 0, 0))                              # wrong colour
    flow.apply_result("ui", "T1", _done(), "c1")
    flow.end_turn("ui")
    card = flow.board.card("T1")
    assert card.column == "in_progress" and card.review_round == 0           # no review spent
    assert any(e["kind"] == "review_issue" and "differ" in e["text"] for e in card.thread)
    assert flow.has_work("ui") and not flow.has_work("reviewer")

    flow.begin_turn("ui")
    _png(tools / "screens" / "2.png", (255, 255, 255))                       # fixed
    flow.apply_result("ui", "T1", _done(), "c2")
    flow.end_turn("ui")
    assert card.column == "review"

    review = flow.begin_turn("reviewer")
    assert review is not None and ".guudflow/review/T1/current.png" in (review.task_text or "")
    assert "read tool" in (review.task_text or "")                          # reviewer has vision
    assert (tmp_path / "tools" / "reviewer" / "review" / "T1" / "current.png").is_file()
    flow.apply_result("reviewer", "T1", ReviewResult(
        verdict="pass", task_id="T1", items=[ItemVerdict(item=ITEM, met=True)], summary="ok"), "")
    assert card.column == "done"
    assert (tmp_path / "run" / "baselines" / "tasks" / "T1.png").is_file()


def test_precheck_gives_up_to_the_reviewer_after_repeated_fails(tmp_path: Path) -> None:
    flow, _ = _setup(tmp_path)
    for _ in range(3):
        flow.begin_turn("ui")
        flow.apply_result("ui", "T1", _done(), "c")                           # never a screenshot
        flow.end_turn("ui")
    assert flow.board.card("T1").column == "review"


def test_human_visual_feedback_lands_on_the_active_card(tmp_path: Path) -> None:
    flow, ws = _setup(tmp_path)
    ws.subscribe(flow.on_signal)
    flow.begin_turn("ui")
    ws.post_signal(Signal(type=SignalType.SCREENSHOT_READY, summary="does this look right?",
                          from_agent="ui", detail=json.dumps({"request_id": "rq1"})))
    ws.post_signal(Signal(type=SignalType.USER_VISUAL_FEEDBACK, summary="feedback",
                          detail=json.dumps({"request_id": "rq1",
                                             "feedback": "Logo should be on the left."})))
    assert any(e["kind"] == "human_comment" and "Logo" in e["text"]
               for e in flow.board.card("T1").thread)


def test_image_budget_caps_screenshots() -> None:
    budget = ImageBudget(per_turn=2, per_run=3)
    calls: list[str] = []

    async def shoot(identity: McpIdentity, args: dict[str, object]) -> ToolResult:
        calls.append(identity.role)
        return ToolResult("ok")

    tool = capped(McpTool("screenshot", "d", {"type": "object"}, shoot), budget)
    ident = McpIdentity("r", "ui", frozenset({"screenshot"}))

    async def run() -> list[ToolResult]:
        budget.new_turn("ui")
        out = [await tool.handler(ident, {}) for _ in range(3)]
        budget.new_turn("ui")
        out += [await tool.handler(ident, {}) for _ in range(2)]
        return out

    results = asyncio.run(run())
    assert [r.is_error for r in results] == [False, False, True, False, True]
    assert "this turn" in results[2].text and "run's image budget" in results[4].text
    assert calls == ["ui", "ui", "ui"]
