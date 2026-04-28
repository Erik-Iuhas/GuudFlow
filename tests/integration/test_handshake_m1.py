"""M1 handshake protocol — end-to-end integration scenarios.

Covers the spec §14 verification gate: a 3-agent run where backend ASKs
architect, the runner dispatches architect on a respond task, the ANSWER
routes back via the resume subscriber, and the run completes APPROVED.

These are smaller-scope versions of the spec scenarios — they exercise the
plumbing (interrupt → dispatch → resume) without needing a full git
workspace. The git/branch tests live elsewhere.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.llm import MockLLMClient, MockTurn
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import WaitingForPeer
from core.signals import SignalType
from core.tool_manifest import RoleRBAC


def _project(**overrides) -> ProjectConfig:
    base = dict(
        project_id="m1",
        goal="ship the auth endpoint",
        agents=[],
        rbac={},
        max_iterations=3,
    )
    base.update(overrides)
    return ProjectConfig(**base)


# ── ASK → ANSWER round-trip via signal subscriber ────────────────────────────


async def test_answer_signal_resumes_asker(tmp_path: Path):
    """When an ANSWER lands on the board with a matching ask_id, the runner's
    resume subscriber routes it to scheduler.resume() and the asker unblocks.
    """
    from core.workspace import Workspace
    from core.scheduler import Scheduler, AgentSpec, WaitingForPeer
    import json
    from core.signals import Signal

    proj = _project(
        agents=[AgentConfig(role="dev", description="codes")],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
    )
    runner = AgentRunner(
        project=proj, run_id="r1", run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    # Wire a scheduler so the subscriber has somewhere to route.
    spec = AgentSpec(role="dev", runner=lambda ws: asyncio.sleep(0))
    scheduler = Scheduler([spec], runner.workspace)
    runner._scheduler = scheduler

    # Simulate the asker parking on a peer interrupt.
    ask_id = "ask-001"
    token = scheduler.interrupt(
        "dev", "peer",
        WaitingForPeer(target_role="tech_lead", ask_id=ask_id),
    )
    runner._open_interrupts["dev"] = token

    # Now post an ANSWER as if tech_lead responded.
    runner.workspace.post_signal(Signal(
        type=SignalType.AGENT_MESSAGE,
        summary="ANSWER tech_lead → dev",
        from_agent="tech_lead",
        detail=json.dumps({
            "ask_id": ask_id, "from": "tech_lead", "to": "dev",
            "answer": "use POST /v2",
        }),
    ))

    # Subscriber should have resumed the dev role.
    from core.workspace import AgentState
    assert runner.workspace.agent_states["dev"] == AgentState.READY
    queued = scheduler.drain_pending_input("dev")
    assert queued == ["use POST /v2"]
    assert "dev" not in runner._open_interrupts


async def test_skip_granted_signal_marks_sat_out(tmp_path: Path):
    """SKIP_GRANTED on the board → runner subscriber → scheduler.mark_sat_out()."""
    from core.scheduler import Scheduler, AgentSpec, WaitingForSkipConfirm
    from core.signals import Signal
    from core.workspace import AgentState

    proj = _project(
        agents=[AgentConfig(role="frontend", description="ui")],
        rbac={"frontend": RoleRBAC(role="frontend", allowed_scopes=[])},
    )
    runner = AgentRunner(
        project=proj, run_id="r2", run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    spec = AgentSpec(role="frontend", runner=lambda ws: asyncio.sleep(0))
    scheduler = Scheduler([spec], runner.workspace)
    runner._scheduler = scheduler

    skip_id = "skip-1"
    token = scheduler.interrupt(
        "frontend", "skip",
        WaitingForSkipConfirm(confirmer_role="tech_lead", skip_id=skip_id),
    )
    runner._open_interrupts["frontend"] = token

    runner.workspace.post_signal(Signal(
        type=SignalType.PROTOCOL,
        summary=f"SKIP_GRANTED frontend",
        from_agent="tech_lead",
        detail=f"skip_id={skip_id};note=no UI in scope",
    ))

    assert runner.workspace.agent_states["frontend"] == AgentState.SAT_OUT
    assert "frontend" in scheduler._sat_out


async def test_skip_denied_signal_resumes_with_redirected_task(tmp_path: Path):
    """SKIP_DENIED → scheduler.resume() with the redirected_task value queued."""
    from core.scheduler import Scheduler, AgentSpec, WaitingForSkipConfirm
    from core.signals import Signal
    from core.workspace import AgentState

    proj = _project(
        agents=[AgentConfig(role="frontend", description="ui")],
        rbac={"frontend": RoleRBAC(role="frontend", allowed_scopes=[])},
    )
    runner = AgentRunner(
        project=proj, run_id="r3", run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    spec = AgentSpec(role="frontend", runner=lambda ws: asyncio.sleep(0))
    scheduler = Scheduler([spec], runner.workspace)
    runner._scheduler = scheduler

    skip_id = "skip-2"
    token = scheduler.interrupt(
        "frontend", "skip",
        WaitingForSkipConfirm(confirmer_role="tech_lead", skip_id=skip_id),
    )
    runner._open_interrupts["frontend"] = token

    runner.workspace.post_signal(Signal(
        type=SignalType.PROTOCOL,
        summary="SKIP_DENIED frontend",
        from_agent="tech_lead",
        detail=f"skip_id={skip_id};redirected_task=update the login form",
    ))

    assert runner.workspace.agent_states["frontend"] == AgentState.READY
    queued = scheduler.drain_pending_input("frontend")
    assert queued == ["update the login form"]


# ── Stories ──────────────────────────────────────────────────────────────────


async def test_story_seeds_open_at_run_start(tmp_path: Path):
    """Project with story seeds → runner posts STORY_OPENED before the
    scheduler runs and populates workspace.stories."""
    proj = _project(
        agents=[
            AgentConfig(role="dev", description="codes"),
        ],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
        stories=[{
            "name": "Auth API",
            "goal": "ship signup and login endpoints",
            "owner_role": "dev",
            "parent_story_id": None,
        }],
    )
    runner = AgentRunner(
        project=proj, run_id="r4", run_root=tmp_path / "run",
        llm=MockLLMClient([
            MockTurn(text="[COMMIT]\n  summary: signup endpoint added\n  files: (none)"),
        ]),
    )
    await runner.run()

    # Story was opened.
    assert len(runner.workspace.stories) == 1
    story = next(iter(runner.workspace.stories.values()))
    assert story.name == "Auth API"
    assert story.owner_role == "dev"
    assert runner.workspace.active_story.get("dev") == story.story_id

    # STORY_OPENED appears on the board.
    opens = [s for s in runner.workspace.board
             if s.type is SignalType.PROTOCOL and s.summary.startswith("STORY_OPENED")]
    assert len(opens) == 1


async def test_open_stories_close_as_deferred_at_run_end(tmp_path: Path):
    """Stories still open at run end are auto-closed with outcome=deferred."""
    proj = _project(
        agents=[AgentConfig(role="dev", description="codes")],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
        stories=[{"name": "Settings UI", "goal": "ship the settings page"}],
    )
    runner = AgentRunner(
        project=proj, run_id="r5", run_root=tmp_path / "run",
        llm=MockLLMClient([
            MockTurn(text="[COMMIT]\n  summary: stub created\n  files: (none)"),
        ]),
    )
    await runner.run()

    story = next(iter(runner.workspace.stories.values()))
    assert story.closed is True
    assert story.closed_outcome == "deferred"

    closes = [s for s in runner.workspace.board
              if s.type is SignalType.PROTOCOL and s.summary.startswith("STORY_CLOSED")]
    assert len(closes) == 1
    assert "deferred" in closes[0].summary


# ── Quip authorship: ASK names persona, not "System" ─────────────────────────


async def test_ask_quip_emits_with_persona_voice(tmp_path: Path):
    """When a runner emits an ASK quip, the asker's persona name is used —
    never 'System' (M1 §11)."""
    from core.quip_engine import QuipEngine, AgentVoice
    import json

    proj = _project(
        agents=[
            AgentConfig(role="dev", description="codes"),
            AgentConfig(role="tech_lead", description="leads"),
        ],
        rbac={
            "dev": RoleRBAC(role="dev", allowed_scopes=[]),
            "tech_lead": RoleRBAC(role="tech_lead", allowed_scopes=[]),
        },
    )

    quip_llm = MockLLMClient([])  # emit_quip path doesn't call LLM
    quip_engine = QuipEngine(llm=quip_llm, log_path=tmp_path / "quips.json")
    quip_engine.set_voices([
        AgentVoice(role="dev", name="Tom", character="careful coder"),
        AgentVoice(role="tech_lead", name="Alex", character="senior lead"),
    ])

    await quip_engine.emit_quip(
        persona="dev",
        text="Hey Alex, what's the schema?",
        addressed_to="tech_lead",
        event_type="peer_question",
    )

    quips = quip_engine.quips()
    assert len(quips) == 1
    assert quips[0].persona == "Tom"
    assert "Alex" in quips[0].text
    assert quips[0].event == "peer_question"
    # Critical assertion: never 'System' for peer events.
    assert quips[0].persona != "System"
