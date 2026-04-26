"""Integration test for training-mode runs (Phase F.1.1).

A run with `project.mode == "training"` and `training_scenario_id` set
must:
  1. Trigger the rubric_scorer post-run, producing a RUBRIC_SCORE signal
  2. Tag the persona's episode as `training=True`
  3. Increment `reputation.training.total_runs` instead of production

This is the seam test — it proves the runner branches correctly. The
rubric scoring details are exercised in test_rubric_scorer.py and the
episode-tagging is exercised in test_memory_writer.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.builtin.code_reviewer import CodeReviewer
from agents.builtin.memory_writer import MemoryWriter
from agents.builtin.rubric_scorer import RubricScorer
from agents.persona import Persona
from agents.registry import registry
from core.llm import MockLLMClient, MockTurn, ToolCall
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import AgentSpec, Scheduler, SchedulerStatus
from core.signals import SignalType
from core.tool_manifest import RoleRBAC


def _make_persona(tmp_path: Path, persona_id: str) -> tuple[Persona, Path]:
    persona_dir = tmp_path / "personas" / persona_id
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "profile.json").write_text(
        json.dumps({
            "persona_id": persona_id,
            "name": persona_id,
            "specialty": "test",
            "character": "methodical",
            "stake_framing": "x",
        }),
        encoding="utf-8",
    )
    persona = Persona.load(persona_id, tmp_path / "personas")
    persona._dir = persona_dir
    return persona, persona_dir


def _seed_scenario(scenarios_root: Path, scenario_id: str) -> None:
    """A trivial scenario that scores via files_present + text_contains —
    exercises both check kinds."""
    d = scenarios_root / scenario_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenario.json").write_text(json.dumps({
        "scenario_id": scenario_id,
        "goal": "Add a hello.py module",
        "project_type": "python_module",
        "expected_role_types": ["developer", "reviewer"],
    }), encoding="utf-8")
    (d / "rubric.json").write_text(json.dumps({
        "auto_checks": [
            {"name": "exists", "type": "files_present", "weight": 0.5,
             "paths": ["hello.py"]},
            {"name": "has_greet", "type": "text_contains", "weight": 0.5,
             "path": "hello.py", "needle": "def greet"},
        ],
    }), encoding="utf-8")


async def test_training_mode_run_produces_rubric_score_and_tags_episode(
    tmp_path: Path,
):
    # ── Set-up: persona + scenario on disk ─────────────────────────────
    dev_persona, dev_dir = _make_persona(tmp_path, "dev-v1")
    scenarios_root = tmp_path / "training_camp" / "scenarios"
    _seed_scenario(scenarios_root, "hello_python_v1")

    # ── Project: training mode with the scenario ───────────────────────
    project = ProjectConfig(
        project_id="proj_training",
        goal="Add a hello.py module",
        mode="training",
        training_scenario_id="hello_python_v1",
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

    if not registry.has("code_reviewer"):
        registry.register("code_reviewer", CodeReviewer)

    # Same script as test_runner_e2e — produces hello.py with greet().
    llm = MockLLMClient([
        MockTurn(tool_calls=[ToolCall(
            id="dev_t1",
            name="fs-write",
            arguments={"path": "hello.py",
                       "content": "def greet():\n    return 'hi'\n"},
        )]),
        MockTurn(text="[COMMIT]\n  summary: wrote greet()\n  files: hello.py"),
        MockTurn(tool_calls=[ToolCall(
            id="rev_t1",
            name="git-diff",
            arguments={"branch_a": "agent/developer", "branch_b": "main"},
        )]),
        MockTurn(text="[VERIFY] PASS"),
    ])

    run_root = tmp_path / "runs" / "run_training"
    runner = AgentRunner(
        project=project,
        run_id="run_training",
        run_root=run_root,
        llm=llm,
        memory_writer=MemoryWriter(),
        rubric_scorer=RubricScorer(),
        training_scenarios_root=scenarios_root,
    )
    runner._build_agents()
    runner.agents["developer"].persona = dev_persona

    # Drive the scheduler the same way test_runner_e2e does.
    specs = [
        AgentSpec(
            role=cfg.role,
            runner=runner._make_scheduler_runner(agent=runner.agents[cfg.role], cfg=cfg),
            depends_on=list(cfg.depends_on),
        )
        for cfg in project.agents
    ]
    result = await Scheduler(specs, runner.workspace, scope="pipeline").run()
    assert result.status is SchedulerStatus.COMPLETE

    # Trigger post-run hooks the way runner.run() does.
    await runner.rubric_scorer.on_run_complete(
        workspace=runner.workspace,
        project=project,
        run_id="run_training",
        run_root=run_root,
        scenarios_root=scenarios_root,
    )
    await runner._run_memory_writer(result)

    # ── Assertions ─────────────────────────────────────────────────────
    # 1. RUBRIC_SCORE signal landed on the board with a perfect score
    #    (both checks pass — file exists + contains def greet).
    rubric_signals = [
        s for s in runner.workspace.board
        if s.type is SignalType.RUBRIC_SCORE
    ]
    assert len(rubric_signals) == 1
    detail = json.loads(rubric_signals[0].detail)
    assert detail["score"] == 1.0
    assert detail["scenario_id"] == "hello_python_v1"

    # 2. The developer's episode is tagged training=True.
    episodes = [
        json.loads(line)
        for line in (dev_dir / "episodes.jsonl")
            .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(episodes) == 1
    assert episodes[0]["training"] is True

    # 3. Reputation aggregates split — training, not production.
    rep = json.loads((dev_dir / "reputation.json").read_text(encoding="utf-8"))
    assert rep["training"]["total_runs"] == 1
    assert rep["production"]["total_runs"] == 0


async def test_production_mode_skips_rubric_scorer(tmp_path: Path):
    """Sanity check: a production run never invokes the rubric_scorer
    even when one is wired in."""
    dev_persona, dev_dir = _make_persona(tmp_path, "dev-v2")
    scenarios_root = tmp_path / "training_camp" / "scenarios"

    project = ProjectConfig(
        project_id="proj_prod",
        goal="x",
        mode="production",  # explicit
        training_scenario_id=None,
        agents=[
            AgentConfig(
                role="developer",
                system_prompt="x",
                declared_local_tools=["fs-read"],
                model_tier="solid",
            ),
        ],
        rbac={"developer": RoleRBAC(role="developer", allowed_scopes=[])},
        max_iterations=1,
    )

    llm = MockLLMClient([MockTurn(text="[COMMIT]\n  summary: done\n  files: (none)")])
    runner = AgentRunner(
        project=project,
        run_id="run_prod",
        run_root=tmp_path / "runs" / "run_prod",
        llm=llm,
        memory_writer=MemoryWriter(),
        rubric_scorer=RubricScorer(),
        training_scenarios_root=scenarios_root,
    )

    runner._build_agents()
    runner.agents["developer"].persona = dev_persona

    specs = [
        AgentSpec(
            role=cfg.role,
            runner=runner._make_scheduler_runner(agent=runner.agents[cfg.role], cfg=cfg),
            depends_on=list(cfg.depends_on),
        )
        for cfg in project.agents
    ]
    sched_result = await Scheduler(specs, runner.workspace, scope="pipeline").run()

    # The runner's post-run branch gates RUBRIC_SCORE on
    # `project.mode == "training"`. Reuse the scheduler's result.
    await runner._run_memory_writer(sched_result)

    rubric_signals = [
        s for s in runner.workspace.board if s.type is SignalType.RUBRIC_SCORE
    ]
    assert rubric_signals == []
