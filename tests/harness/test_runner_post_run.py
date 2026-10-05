"""AgentRunner post-run hooks on the opencode harness (S15).

These tests used to drive the legacy in-process loop. They now run whole
projects through real opencode against the fake LLM and check what the runner
does around the run: persona memory and reputation, training rubric scoring,
the quip engine's closing flourish, and team validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents.builtin.memory_writer import MemoryWriter
from agents.builtin.rubric_scorer import RubricScorer
from core.harness.config_gen import ProviderBinding
from core.harness.run import HarnessSettings
from core.llm import MockLLMClient, MockTurn
from core.llm.registry import ModelInfo
from core.quip_engine import QuipEngine
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import SignalType
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, Text, ToolCall

pytestmark = requires_opencode

FAKE_MODEL = ModelInfo(id="fake-model", provider="fake", tier="solid", supports_images=True)


def _settings(fake: FakeLLM) -> HarnessSettings:
    return HarnessSettings(
        production=False, turn_timeout=60, sandbox="host",
        binding_factory=lambda cfg: ProviderBinding("fake", FAKE_MODEL, "openai_compatible",
                                                    base_url=fake.base_url, api_key="fake"))


def _make_persona(tmp_path: Path, persona_id: str, name: str) -> Path:
    persona_dir = tmp_path / "personas" / persona_id
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "profile.json").write_text(json.dumps({
        "persona_id": persona_id, "name": name, "specialty": "test specialist",
        "character": "methodical", "stake_framing": "this matters",
        "created_at": "2026-04-25T00:00:00Z",
    }), encoding="utf-8")
    return persona_dir


def _done(summary: str, files: list[str]) -> ToolCall:
    return ToolCall("guudflow_submit_result",
                    {"status": "done", "summary": summary, "files_changed": files})


def _script_hello(fake: FakeLLM) -> None:
    """The builder writes hello.py; the reviewer reads it and passes."""
    fake.route("Your turn: developer",
               ToolCall("write", {"filePath": "hello.py",
                                  "content": "def greet():\n    return 'hi'\n"}),
               _done("Wrote greet() in hello.py returning a greeting.", ["hello.py"]),
               Text("Done."))
    fake.route("Your turn: reviewer",
               ToolCall("read", {"filePath": "hello.py"}),
               ToolCall("guudflow_submit_result", {"verdict": "pass", "issues": []}),
               Text("Reviewed."))


def _team() -> list[AgentConfig]:
    return [
        AgentConfig(role="developer", description="Writes the code", persona_id="dev-v1"),
        AgentConfig(role="reviewer", description="Reviews the developer's work",
                    depends_on=["developer"], is_reviewer=True, persona_id="rev-v1"),
    ]


async def test_two_agent_run_records_memory_and_reputation(tmp_path: Path,
                                                           fake_llm: FakeLLM) -> None:
    dev_dir = _make_persona(tmp_path, "dev-v1", "Dev")
    rev_dir = _make_persona(tmp_path, "rev-v1", "Rev")
    _script_hello(fake_llm)
    runner = AgentRunner(
        project=ProjectConfig(project_id="proj_e2e", goal="Add a hello.py module",
                              agents=_team(), harness="opencode"),
        run_id="run_e2e", run_root=tmp_path / "runs" / "run_e2e", max_wall_seconds=300,
        memory_writer=MemoryWriter(), personas_root=tmp_path / "personas")
    runner.harness_settings = _settings(fake_llm)

    result = await runner.run()

    assert result.status is SchedulerStatus.COMPLETE, runner.workspace.board
    assert (runner.git.working_dir / "hello.py").exists()
    types = [s.type for s in runner.workspace.board]
    assert SignalType.GIT_MERGE in types
    assert SignalType.AGENT_RESULT in types
    # Hired personas and commit authors are recorded for the quip feed and git log.
    assert runner.workspace.hired_personas == {"developer": "dev-v1", "reviewer": "rev-v1"}
    # The memory writer wrote one episode per persona and refreshed reputation.
    for persona_dir in (dev_dir, rev_dir):
        episodes = (persona_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(episodes) == 1
        reputation = json.loads((persona_dir / "reputation.json").read_text(encoding="utf-8"))
        assert reputation["total_episodes"] == 1
        assert reputation["production"]["total_runs"] == 1
    # The retrospective is part of the run's output.
    assert list((tmp_path / "runs" / "run_e2e").glob("retrospective*"))


def _seed_scenario(scenarios_root: Path, scenario_id: str) -> None:
    d = scenarios_root / scenario_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "scenario.json").write_text(json.dumps({
        "scenario_id": scenario_id, "goal": "Add a hello.py module",
        "project_type": "python_module", "expected_role_types": ["developer", "reviewer"],
    }), encoding="utf-8")
    (d / "rubric.json").write_text(json.dumps({"auto_checks": [
        {"name": "exists", "type": "files_present", "weight": 0.5, "paths": ["hello.py"]},
        {"name": "has_greet", "type": "text_contains", "weight": 0.5,
         "path": "hello.py", "needle": "def greet"},
    ]}), encoding="utf-8")


async def test_training_run_scores_the_rubric_and_tags_the_episode(tmp_path: Path,
                                                                   fake_llm: FakeLLM) -> None:
    dev_dir = _make_persona(tmp_path, "dev-v1", "Dev")
    _make_persona(tmp_path, "rev-v1", "Rev")
    scenarios_root = tmp_path / "training_camp" / "scenarios"
    _seed_scenario(scenarios_root, "hello_python_v1")
    _script_hello(fake_llm)
    runner = AgentRunner(
        project=ProjectConfig(project_id="proj_training", goal="Add a hello.py module",
                              mode="training", training_scenario_id="hello_python_v1",
                              agents=_team(), harness="opencode"),
        run_id="run_training", run_root=tmp_path / "runs" / "run_training",
        max_wall_seconds=300, memory_writer=MemoryWriter(), rubric_scorer=RubricScorer(),
        training_scenarios_root=scenarios_root, personas_root=tmp_path / "personas")
    runner.harness_settings = _settings(fake_llm)

    result = await runner.run()

    assert result.status is SchedulerStatus.COMPLETE, runner.workspace.board
    rubric = [s for s in runner.workspace.board if s.type is SignalType.RUBRIC_SCORE]
    assert len(rubric) == 1
    detail = json.loads(rubric[0].detail)
    assert detail["score"] == 1.0
    assert detail["scenario_id"] == "hello_python_v1"
    episodes = [json.loads(line) for line in
                (dev_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    assert len(episodes) == 1 and episodes[0]["training"] is True
    reputation = json.loads((dev_dir / "reputation.json").read_text(encoding="utf-8"))
    assert reputation["training"]["total_runs"] == 1
    assert reputation["production"]["total_runs"] == 0


async def test_production_run_never_scores_a_rubric(tmp_path: Path, fake_llm: FakeLLM) -> None:
    _make_persona(tmp_path, "dev-v1", "Dev")
    _make_persona(tmp_path, "rev-v1", "Rev")
    _script_hello(fake_llm)
    runner = AgentRunner(
        project=ProjectConfig(project_id="proj_prod", goal="Add a hello.py module",
                              mode="production", agents=_team(), harness="opencode"),
        run_id="run_prod", run_root=tmp_path / "runs" / "run_prod", max_wall_seconds=300,
        rubric_scorer=RubricScorer(), training_scenarios_root=tmp_path / "scenarios",
        personas_root=tmp_path / "personas")
    runner.harness_settings = _settings(fake_llm)

    result = await runner.run()

    assert result.status is SchedulerStatus.COMPLETE
    assert not [s for s in runner.workspace.board if s.type is SignalType.RUBRIC_SCORE]


async def test_quip_engine_gets_voices_and_closes_the_run(tmp_path: Path,
                                                          fake_llm: FakeLLM) -> None:
    _make_persona(tmp_path, "dev-v1", "Dev")
    _make_persona(tmp_path, "rev-v1", "Rev")
    _script_hello(fake_llm)
    quip_log = tmp_path / "quip_log.json"
    engine = QuipEngine(llm=MockLLMClient([MockTurn(text="Shipped it.")] * 4), log_path=quip_log)
    runner = AgentRunner(
        project=ProjectConfig(project_id="proj_quip", goal="Add a hello.py module",
                              agents=_team(), harness="opencode"),
        run_id="run_quip", run_root=tmp_path / "runs" / "run_quip", max_wall_seconds=300,
        quip_engine=engine, personas_root=tmp_path / "personas")
    runner.harness_settings = _settings(fake_llm)

    result = await runner.run()

    assert result.status is SchedulerStatus.COMPLETE
    voices = {v.role: v for v in engine._voices}  # noqa: SLF001 — checking the runner's wiring
    assert set(voices) == {"developer", "reviewer"}
    assert voices["developer"].name == "Dev"        # persona-backed role speaks as the persona
    assert voices["reviewer"].name == "Rev"
    quips: list[dict[str, Any]] = json.loads(quip_log.read_text(encoding="utf-8"))
    assert any(q["event"] == "run_complete" for q in quips)


async def test_dependency_cycle_fails_before_anything_spawns(tmp_path: Path,
                                                             fake_llm: FakeLLM) -> None:
    runner = AgentRunner(
        project=ProjectConfig(project_id="p", goal="x", harness="opencode", agents=[
            AgentConfig(role="a", depends_on=["b"]), AgentConfig(role="b", depends_on=["a"])]),
        run_id="r", run_root=tmp_path / "run")
    runner.harness_settings = _settings(fake_llm)
    with pytest.raises(RuntimeError, match="dependency cycle detected"):
        await runner.run()
    assert not fake_llm.requests
    assert any(s.type is SignalType.FAILED and "cycle" in s.summary.lower()
               for s in runner.workspace.board)
