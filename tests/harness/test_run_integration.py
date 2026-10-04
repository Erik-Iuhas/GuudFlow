"""P1 integration: AgentRunner with harness="opencode", real opencode, fake LLM.

Each test runs a whole project through the scheduler: per-role clones, one
opencode server per role, the per-run MCP server, and submit_result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.harness.config_gen import ProviderBinding
from core.harness.run import HarnessSettings
from core.llm.registry import ModelInfo
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import SignalType
from tests.harness.conftest import requires_opencode
from tests.harness.fake_llm import FakeLLM, ToolCall, fake_provider_config

pytestmark = requires_opencode

FAKE_MODEL = ModelInfo(id="fake-model", provider="fake", tier="solid", supports_images=True)


def _settings(fake: FakeLLM, **kw: Any) -> HarnessSettings:
    return HarnessSettings(
        production=False, turn_timeout=60,
        binding_factory=lambda cfg: ProviderBinding("fake", FAKE_MODEL, "openai_compatible",
                                                    base_url=fake.base_url, api_key="fake"),
        extra_providers=fake_provider_config(fake.base_url)["provider"], **kw)


def _runner(tmp_path: Path, agents: list[AgentConfig], fake: FakeLLM) -> AgentRunner:
    project = ProjectConfig(project_id="p", goal="Build a tiny greeting module.",
                            agents=agents, harness="opencode")
    runner = AgentRunner(project=project, run_id="run1", run_root=tmp_path / "run",
                         max_wall_seconds=300)
    runner.harness_settings = _settings(fake)
    return runner


def _done(summary: str, files: list[str] | None = None) -> ToolCall:
    return ToolCall("guudflow_submit_result",
                    {"status": "done", "summary": summary, "files_changed": files or []})


def _types(runner: AgentRunner, role: str | None = None) -> list[SignalType]:
    return [s.type for s in runner.workspace.board if role is None or s.from_agent == role]


async def test_single_builder_end_to_end(tmp_path: Path, fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: builder",
                   ToolCall("write", {"filePath": "greet.py",
                                      "content": "def greet(n):\n    return f'hi {n}'\n"}),
                   _done("Added greet() in greet.py returning a greeting.", ["greet.py"]))
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE, runner.workspace.board
    main = runner.git.working_dir
    assert (main / "greet.py").read_text().startswith("def greet")
    kinds = _types(runner, "builder")
    for expected in (SignalType.STARTED, SignalType.CAPABILITIES, SignalType.AGENT_RESULT,
                     SignalType.COMPLETE):
        assert expected in kinds
    assert SignalType.GIT_MERGE in _types(runner)
    assert runner.workspace.artifacts["builder"].type == "agent_result"


async def test_chain_builder_then_reviewer_sees_upstream(tmp_path: Path, fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: builder",
                   ToolCall("write", {"filePath": "greet.py", "content": "x = 1\n"}),
                   _done("Wrote greet.py with the module skeleton.", ["greet.py"]))
    fake_llm.route("Your turn: reviewer",
                   ToolCall("read", {"filePath": "greet.py"}),
                   ToolCall("guudflow_submit_result", {"verdict": "pass", "issues": []}))
    runner = _runner(tmp_path, [AgentConfig(role="builder"),
                                AgentConfig(role="reviewer", is_reviewer=True,
                                            depends_on=["builder"])], fake_llm)
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE
    reviewer_brief = next(str(r["messages"]) for r in fake_llm.requests
                          if "Your turn: reviewer" in str(r["messages"]))
    assert "Wrote greet.py with the module skeleton." in reviewer_brief
    # The reviewer's own clone already had the builder's work merged in.
    read_call = [r for r in fake_llm.tool_requests() if "Your turn: reviewer" in str(r["messages"])]
    assert "x = 1" in str(read_call[1]["messages"])


async def test_invalid_then_missing_result_fails_after_one_retry(tmp_path: Path,
                                                                 fake_llm: FakeLLM) -> None:
    from tests.harness.fake_llm import Text
    fake_llm.route("Your turn: builder",
                   ToolCall("guudflow_submit_result", {"status": "done", "summary": "short"}),
                   Text("I give up."),
                   Text("Still nothing."))
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    result = await runner.run()
    assert "builder" in result.failed
    failed = [s for s in runner.workspace.board
              if s.type is SignalType.FAILED and s.from_agent == "builder"]
    assert failed and "summary" in (failed[-1].detail or "")
    retry = [r for r in fake_llm.tool_requests() if "without an accepted result" in str(r["messages"])]
    assert retry, "exactly one retry prompt is sent"


async def test_protected_path_edit_fails_the_turn(tmp_path: Path, fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: builder",
                   ToolCall("bash", {"command": "git status", "description": "look"}),
                   ToolCall("write", {"filePath": "inputs/spec.md", "content": "rewritten"}),
                   _done("Rewrote the spec to match what I built."))
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    result = await runner.run()
    # opencode's edit rule denies inputs/** before anything is written.
    assert result.status is SchedulerStatus.COMPLETE
    assert not (runner.git.working_dir / "inputs" / "spec.md").exists()


async def test_notes_reach_the_next_role(tmp_path: Path, fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: planner",
                   ToolCall("guudflow_post_note", {"to_role": "builder",
                                                   "text": "Use snake_case names please."}),
                   ToolCall("guudflow_submit_result",
                            {"status": "done", "summary": "Planned the module layout for builder.",
                             "notes_for": {"builder": "Keep it under 50 lines."}}))
    fake_llm.route("Your turn: builder", _done("Implemented the module as planned."))
    runner = _runner(tmp_path, [AgentConfig(role="planner"),
                                AgentConfig(role="builder", depends_on=["planner"])], fake_llm)
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE
    builder_brief = next(str(r["messages"]) for r in fake_llm.requests
                         if "Your turn: builder" in str(r["messages"]))
    assert "Use snake_case names please." in builder_brief
    assert "Keep it under 50 lines." in builder_brief
    assert "untrusted" in builder_brief


@pytest.mark.parametrize("production", [True])
async def test_host_dev_refused_in_production(tmp_path: Path, fake_llm: FakeLLM,
                                             production: bool) -> None:
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    runner.harness_settings = _settings(fake_llm)
    runner.harness_settings.production = production
    with pytest.raises(Exception, match="host_dev"):
        await runner.run()
