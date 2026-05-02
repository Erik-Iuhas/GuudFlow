"""End-to-end Stage 5 test: 2-agent run with mocked LLM.

Exit criterion (CLAUDE.md Stage 5):
> A 2-agent run completes end-to-end with protocol messages on the board,
> git commits in workspace, quips in quip log; memory_writer creates a new
> episode in episodes.jsonl, updates pattern confidence if applicable, and
> refreshes reputation.json post-run.

This test runs a `developer` (prompt-defined) and a `reviewer`
(CodeReviewer builtin) using a scripted MockLLMClient. The developer
writes a file, the reviewer approves, and the run completes. We
verify: signals on the board, git commits made, episode written,
reputation updated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.builtin.code_reviewer import CodeReviewer
from agents.builtin.memory_writer import MemoryWriter
from agents.persona import Persona
from core.llm import LLMMessage, MockLLMClient, MockTurn, ToolCall
from core.runner import (
    AgentConfig,
    AgentRunner,
    ProjectConfig,
)
from core.scheduler import SchedulerStatus
from core.signals import SignalType
from core.tool_manifest import RoleRBAC


def _make_persona(tmp_path: Path, persona_id: str, name: str = "Aria") -> tuple[Persona, Path]:
    """Materialise a fresh persona in a temp dir and load it."""
    persona_dir = tmp_path / "personas" / persona_id
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "profile.json").write_text(
        json.dumps({
            "persona_id": persona_id,
            "name": name,
            "specialty": "test specialist",
            "character": "methodical",
            "stake_framing": "this matters",
            "created_at": "2026-04-25T00:00:00Z",
        }),
        encoding="utf-8",
    )
    persona = Persona.load(persona_id, tmp_path / "personas")
    persona._dir = persona_dir  # MemoryWriter persistence hook
    return persona, persona_dir


async def test_two_agent_run_e2e(tmp_path: Path):
    # 1. Build personas.
    dev_persona, dev_dir = _make_persona(tmp_path, "dev-v1", name="Dev")
    rev_persona, rev_dir = _make_persona(tmp_path, "rev-v1", name="Rev")

    # 2. Project config: developer → reviewer.
    project = ProjectConfig(
        project_id="proj_e2e",
        goal="Add a hello.py module",
        # Track 4.2 — this test asserts merges land on main. Default flipped
        # to "manual" in 4.2; opt back into auto-merge for legacy assertion.
        pr_mode="auto",
        agents=[
            AgentConfig(
                role="developer",
                description="Writes the code",
                system_prompt="Write code with fs:write.",
                declared_local_tools=["fs-read", "fs-list", "fs-write"],
                model_tier="solid",
            ),
            AgentConfig(
                role="reviewer",
                description="Reviews the developer's work",
                depends_on=["developer"],
                declared_local_tools=["fs-read", "fs-list", "git-diff", "git-log"],
                model_tier="solid",
                agent_class_role_type="code_reviewer",
                is_reviewer=True,
                max_tool_iterations=5,
            ),
        ],
        rbac={
            "developer": RoleRBAC(role="developer", allowed_scopes=["fs-write"]),
            "reviewer": RoleRBAC(role="reviewer", allowed_scopes=[]),
        },
        max_iterations=3,
    )

    # 3. Register CodeReviewer in the registry for this test.
    from agents.registry import registry
    if not registry.has("code_reviewer"):
        registry.register("code_reviewer", CodeReviewer)

    # 4. Scripted LLM responses.
    # Developer turn 1: tool_use fs:write
    # Developer turn 2: end_turn (reports done)
    # Reviewer turn 1: tool_use git:diff
    # Reviewer turn 2: end_turn (APPROVED)
    llm = MockLLMClient([
        MockTurn(tool_calls=[ToolCall(
            id="dev_t1",
            name="fs-write",
            arguments={"path": "hello.py", "content": "def greet():\n    return 'hi'\n"},
        )]),
        MockTurn(text="[COMMIT]\n  summary: wrote greet()\n  files: hello.py"),
        MockTurn(tool_calls=[ToolCall(
            id="rev_t1",
            name="git-diff",
            arguments={"branch_a": "agent/developer", "branch_b": "main"},
        )]),
        MockTurn(text="[VERIFY] PASS"),
    ])

    # 5. Memory writer.
    mw = MemoryWriter()

    # 6. Construct runner.
    run_root = tmp_path / "runs" / "run_e2e"
    runner = AgentRunner(
        project=project,
        run_id="run_e2e",
        run_root=run_root,
        llm=llm,
        memory_writer=mw,
    )

    # 7. Hire personas: post-build, set persona on each agent.
    runner._build_agents()
    runner.agents["developer"].persona = dev_persona
    runner.agents["reviewer"].persona = rev_persona

    # 8. Drive scheduler manually (skip the _build_agents inside run).
    from core.scheduler import AgentSpec, Scheduler
    specs = []
    for cfg in project.agents:
        agent = runner.agents[cfg.role]
        specs.append(AgentSpec(
            role=cfg.role,
            runner=runner._make_scheduler_runner(agent, cfg),
            depends_on=list(cfg.depends_on),
        ))
    scheduler = Scheduler(specs, runner.workspace, scope="pipeline")
    result = await scheduler.run()

    # 9. Trigger memory_writer.
    await runner._run_memory_writer(result)

    # ── Assertions ─────────────────────────────────────────────────────────

    assert result.status is SchedulerStatus.COMPLETE
    assert "developer" in runner.workspace.completed_agents
    assert "reviewer" in runner.workspace.completed_agents

    # File written by developer
    assert await runner.files.exists("hello.py")

    # Git commit made on developer's branch
    dev_log = runner.git.log("agent/developer", limit=5)
    assert any("developer" in e.message or "iteration" in e.message for e in dev_log)

    # Reviewer APPROVED → developer branch merged into main
    main_log = runner.git.log("main", limit=10)
    assert any("merge" in e.message.lower() for e in main_log)

    # Protocol-style or APPROVED signal present
    types = [s.type for s in runner.workspace.board]
    assert SignalType.APPROVED in types
    assert SignalType.GIT_COMMIT in types

    # FILE_CLAIM emitted by fs:write auto-claim
    assert SignalType.FILE_CLAIM in types
    assert SignalType.FILE_RELEASE in types

    # Memory writer wrote an episode for both personas
    dev_episodes = (dev_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    rev_episodes = (rev_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(dev_episodes) == 1
    assert len(rev_episodes) == 1

    # Reputation refreshed
    dev_rep = json.loads((dev_dir / "reputation.json").read_text(encoding="utf-8"))
    assert dev_rep["total_episodes"] == 1
    assert dev_rep["production"]["total_runs"] == 1
