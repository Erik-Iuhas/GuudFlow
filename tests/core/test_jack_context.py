"""Jack anywhere (docs/JACK_UNSTUCK_PLAN.md): the owner's context, the board digest, read tools,
and the buttons Jack may offer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.jack_actions import JackAction, check_actions
from core.jack_context import OwnerContext, ReadTools, digest, load_run, run_digest
from core.llm.client import MockLLMClient, MockTurn, ToolCall
from core.project_manager import TOOL_NAME, ProjectManager
from core.signals import Signal, SignalType
from core.workspace import Task, Workspace
from tests.spec_fixtures import with_reasons

DOC = {"goal": "A tiny game", "agents": [{"role": "planner"}, {"role": "dev", "depends_on": ["planner"]}]}


def _ws() -> Workspace:
    ws = Workspace(run_id="run_x", goal="g")
    ws.add_task(Task(task_id="T1", story_id="plan", assigned_to="dev", title="Game loop",
                     acceptance_criteria=["the loop runs"], column="done"))
    ws.add_task(Task(task_id="T3", story_id="plan", assigned_to="dev", title="Sprite loader",
                     acceptance_criteria=["sprites load"], column="blocked", blocked_on=["planner"],
                     thread=[{"kind": "note", "author": "dev", "text": "which sprite size?"}]))
    ws.post_signal(Signal(type=SignalType.STARTED, from_agent="dev", summary="dev started"))
    ws.post_signal(Signal(type=SignalType.HUMAN_NEEDED, from_agent="dev", summary="Run bun test plain?"))
    return ws


def test_owner_context_keeps_only_clean_ids() -> None:
    ctx = OwnerContext.from_dict({"page": "run", "run_id": "run_x", "card": "T3", "item": "B1; rm -rf"})
    assert ctx is not None and ctx.card == "T3" and ctx.item is None
    assert ctx.label() == "Run · run_x · T3"
    assert OwnerContext.from_dict(None) is None


def test_run_digest_names_waits_running_and_the_open_question(tmp_path: Path) -> None:
    state = load_run(tmp_path, "run_x", _ws())
    assert state is not None
    text = run_digest(state)
    assert "blocked (1): T3 Sprite loader [dev] blocked on planner" in text
    assert "Running now: dev" in text
    assert "Waiting on the owner: dev asks" in text and "bun test" in text


def test_digest_resolves_the_card_and_the_project(tmp_path: Path) -> None:
    (tmp_path / "projects" / "p").mkdir(parents=True)
    ws = _ws()
    ctx = OwnerContext(page="run", run_id="run_x", card="T3")
    text = digest(tmp_path, "p", DOC, ctx, {"run_x": ws}.get)
    assert "The card the owner is looking at:\nT3 Sprite loader" in text
    assert "which sprite size?" in text and "UNTRUSTED" in text.upper()
    assert "Spec v0" in text and "Stack: not set" in text


def test_read_tools_answer_and_refuse_unknown_runs(tmp_path: Path) -> None:
    tools = ReadTools(tmp_path, "p", DOC, workspace_for={"run_x": _ws()}.get)
    assert "Sprite loader" in tools.call("read_card", {"run_id": "run_x", "card": "T3"})
    assert tools.call("read_card", {"run_id": "nope", "card": "T3"}) == "No such run in this project."
    assert "No briefs recorded" in tools.call("read_brief", {"run_id": "run_x", "role": "dev"})


def test_actions_outside_the_project_are_dropped() -> None:
    acts = [JackAction(kind="answer", label="Answer dev", run_id="run_x", text="run bun test plain"),
            JackAction(kind="message_worker", label="Tell ghost", run_id="run_x", role="ghost", text="hi"),
            JackAction(kind="answer", label="Answer", run_id="other", text="x"),
            JackAction(kind="approve_draft", label="Approve")]
    kept = check_actions(acts, roles={"dev"}, run_ids={"run_x"}, items=set(), has_draft=False)
    assert [a.label for a in kept] == ["Answer dev"]


async def test_jack_reads_then_answers_with_a_button(tmp_path: Path) -> None:
    (tmp_path / "projects" / "p").mkdir(parents=True)
    ws = _ws()
    llm = MockLLMClient([
        MockTurn(tool_calls=[ToolCall("r1", "read_card", {"run_id": "run_x", "card": "T3"})]),
        MockTurn(tool_calls=[ToolCall("1", TOOL_NAME, with_reasons({
            "reply": "dev asks whether to run bun test plain. Yes: pipes to sed are refused.",
            "changes": [],
            "actions": [{"kind": "answer", "label": "Answer: run it plain", "run_id": "run_x",
                         "text": "Run bun test plain; pipe only to grep, head or tail."}]}))]),
    ])
    pm = ProjectManager(llm, tmp_path, "p", DOC)
    pm.read_tools = ReadTools(tmp_path, "p", DOC, workspace_for={"run_x": ws}.get)
    pm.run_ids = {"run_x"}
    out = await pm.turn("Why is the run paused?", mode="discuss",
                        owner_context="The owner is on: Run · run_x", context_meta={"page": "run", "run_id": "run_x"})
    assert len(llm.calls) == 2
    tools = {t["name"] for t in llm.calls[0]["tools"]}
    assert {"read_card", "read_brief", TOOL_NAME} <= tools
    second = llm.calls[1]["messages"]
    assert second[-1].content[0]["type"] == "tool_result" and "Sprite loader" in second[-1].content[0]["content"]
    jack = out.messages[-1]
    assert jack["actions"] == [{"kind": "answer", "label": "Answer: run it plain", "run_id": "run_x",
                                "text": "Run bun test plain; pipe only to grep, head or tail.",
                                "items": []}]
    human = out.messages[0]
    assert human["context"] == {"page": "run", "run_id": "run_x"}
    assert "Where the owner is:" in llm.calls[0]["messages"][0].content[0]["text"] + "".join(
        b.get("text", "") for b in llm.calls[0]["messages"][0].content)
