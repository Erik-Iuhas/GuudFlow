"""P1 integration: AgentRunner with harness="opencode", real opencode, fake LLM.

Each test runs a whole project through the scheduler: per-role clones, one
opencode server per role, the per-run MCP server, and submit_result.
"""

from __future__ import annotations

import json
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
from tests.harness.fake_llm import FakeLLM, ToolCall

pytestmark = requires_opencode

FAKE_MODEL = ModelInfo(id="fake-model", provider="fake", tier="solid", supports_images=True)


def _settings(fake: FakeLLM, **kw: Any) -> HarnessSettings:
    return HarnessSettings(
        production=False, turn_timeout=60,
        sandbox="host",
        # Upstream for the gateway: model traffic flows opencode → gateway → FakeLLM.
        binding_factory=lambda cfg: ProviderBinding("fake", FAKE_MODEL, "openai_compatible",
                                                    base_url=fake.base_url, api_key="fake"),
        **kw)


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
    fake_llm.route("Your turn: architect",
                   ToolCall("guudflow_post_note", {"to_role": "builder",
                                                   "text": "Use snake_case names please."}),
                   ToolCall("guudflow_submit_result",
                            {"status": "done", "summary": "Planned the module layout for builder.",
                             "notes_for": {"builder": "Keep it under 50 lines."}}))
    fake_llm.route("Your turn: builder", _done("Implemented the module as planned."))
    runner = _runner(tmp_path, [AgentConfig(role="architect"),
                                AgentConfig(role="builder", depends_on=["architect"])], fake_llm)
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


async def test_bridge_posts_tool_calls_and_narration(tmp_path: Path, fake_llm: FakeLLM) -> None:
    from tests.harness.fake_llm import Turn
    fake_llm.route("Your turn: builder",
                   Turn(calls=[ToolCall("write", {"filePath": "a.py", "content": "x = 1\n"})],
                        text="Creating a.py first."),
                   _done("Created a.py with the constant."))
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    assert (await runner.run()).status is SchedulerStatus.COMPLETE
    board = runner.workspace.board
    assert any(s.type is SignalType.TOOL_CALL and "a.py" in s.summary for s in board)
    assert any(s.type is SignalType.THINKING and "Creating a.py first." in s.summary for s in board)


async def test_shell_ask_goes_through_the_approval_gate(tmp_path: Path, fake_llm: FakeLLM) -> None:
    import asyncio
    from core.signals import Signal
    fake_llm.route("Your turn: builder",
                   ToolCall("bash", {"command": "touch made_by_ask.txt", "description": "x"}),
                   ToolCall("bash", {"command": "touch second.txt", "description": "y"}),
                   _done("Created two files through approved shell commands."))
    runner = _runner(tmp_path, [AgentConfig(role="builder", capabilities={"shell": "ask"})],
                     fake_llm)
    ws = runner.workspace
    asked: list[str] = []

    def approve(sig: Signal) -> None:
        if sig.type is not SignalType.APPROVAL_NEEDED:
            return
        approval_id = (sig.detail or "").split(";")[0].split("=")[1]
        asked.append(approval_id)
        ws.record_approval(approval_id, "granted")
        asyncio.get_running_loop().call_soon(ws.post_signal, Signal(
            type=SignalType.APPROVAL_GRANTED, summary="ok", from_agent="user",
            detail=f"approval_id={approval_id}; kind=tool_permission; scope=run"))

    ws.subscribe(approve)
    assert (await runner.run()).status is SchedulerStatus.COMPLETE
    assert len(asked) == 1, "an 'always this run' grant covers the second touch"
    main = runner.git.working_dir
    assert (main / "made_by_ask.txt").exists() and (main / "second.txt").exists()


def _write_persona(root: Path) -> None:
    d = root / "ada"
    d.mkdir(parents=True)
    (d / "profile.json").write_text(json.dumps({
        "persona_id": "ada", "name": "Ada", "specialty": "Python engineer",
        "character": "Methodical and thorough.", "stake_framing": "Your code ships this week."}))


async def test_persona_prompt_is_compiled_and_recorded(tmp_path: Path, fake_llm: FakeLLM) -> None:
    """S12: the persona's layers reach the model; the board gets only digest + layer names."""
    _write_persona(tmp_path / "personas")
    fake_llm.route("Your turn: builder", _done("Did the work for the builder role."))
    project = ProjectConfig(project_id="p", goal="Build a tiny greeting module.", harness="opencode",
                            agents=[AgentConfig(role="builder", persona_id="ada",
                                                system_prompt="Write clean code.\nEmit [COMMIT] when done.")])
    runner = AgentRunner(project=project, run_id="run1", run_root=tmp_path / "run",
                         max_wall_seconds=300, personas_root=tmp_path / "personas")
    runner.harness_settings = _settings(fake_llm)
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE, runner.workspace.board
    caps = next(s for s in runner.workspace.board if s.type is SignalType.CAPABILITIES)
    prompt = json.loads(caps.detail or "{}")["prompt"]
    assert len(prompt["digest"]) == 16
    assert {"identity", "character", "stake", "base", "cot", "framing"} <= set(prompt["layers"])
    assert prompt["layers"][-1] == "framing" and "memory" not in prompt["layers"]
    sent = str(fake_llm.requests[0]["messages"])
    assert "You are Ada, a Python engineer." in sent and "Write clean code." in sent
    assert "Emit [COMMIT]" not in sent
    assert "Make this something you are proud of." in sent


async def test_single_builder_prompt_layers_without_persona(tmp_path: Path,
                                                            fake_llm: FakeLLM) -> None:
    fake_llm.route("Your turn: builder", _done("Did the work for the builder role."))
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    await runner.run()
    caps = next(s for s in runner.workspace.board if s.type is SignalType.CAPABILITIES)
    layers = json.loads(caps.detail or "{}")["prompt"]["layers"]
    assert layers[0] == "identity" and layers[-1] == "framing" and "character" not in layers


def _when(cond: Any, then: ToolCall, wait: float = 30.0) -> Any:
    """A scripted step that holds the model's reply until `cond()` is true."""
    import time

    def step(_body: dict[str, Any]) -> ToolCall:
        deadline = time.monotonic() + wait
        while not cond() and time.monotonic() < deadline:
            time.sleep(0.05)
        return then
    return step


async def test_parallel_conflict_is_resolved_in_the_same_session(tmp_path: Path,
                                                                 fake_llm: FakeLLM) -> None:
    runner = _runner(tmp_path, [AgentConfig(role="alpha"), AgentConfig(role="beta")], fake_llm)
    beta_file = tmp_path / "run" / "roles" / "beta" / "work" / "same.py"
    main_file = runner.git.working_dir / "same.py"
    # The conflict follow-up quotes the brief, so its route must win: register it first.
    fake_llm.route("now contain conflict markers",
                   ToolCall("write", {"filePath": "same.py", "content": "alpha = 1\nbeta = 2\n"}),
                   _done("Kept both: merged alpha's and beta's lines.", ["same.py"]))
    # beta writes first; alpha writes once beta has, and merges first; beta then
    # submits only after alpha's merge landed, so beta's merge must conflict.
    fake_llm.route("Your turn: beta",
                   ToolCall("write", {"filePath": "same.py", "content": "beta = 2\n"}),
                   _when(main_file.exists, _done("Added beta's line to same.py.", ["same.py"])))
    fake_llm.route("Your turn: alpha",
                   _when(beta_file.exists, ToolCall("write", {"filePath": "same.py",
                                                              "content": "alpha = 1\n"})),
                   _done("Added alpha's line to same.py.", ["same.py"]))
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE, runner.workspace.board
    assert main_file.read_text() == "alpha = 1\nbeta = 2\n"
    conflicts = [s for s in runner.workspace.board if s.type is SignalType.GIT_CONFLICT]
    assert any(s.from_agent == "beta" and "same.py" in (s.detail or "") for s in conflicts)
    assert fake_llm.remaining("now contain conflict markers") == 0


async def test_rejected_branch_does_not_poison_the_next_turn(tmp_path: Path,
                                                             fake_llm: FakeLLM) -> None:
    """A secret in the first turn is set aside; the re-armed turn merges cleanly."""
    from core.harness.run import OpencodeRun
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    fake_llm.route("Your turn: builder",
                   ToolCall("write", {"filePath": "app.py", "content": "print('ok')\n"}),
                   ToolCall("write", {"filePath": "id_rsa",
                                      "content": "-----BEGIN RSA PRIVATE KEY-----\nabc\n"}),
                   _done("Added app.py and a deploy key.", ["app.py", "id_rsa"]))
    result = await runner.run()
    assert "builder" in result.failed                       # role mode: the turn fails...
    run = OpencodeRun(runner, runner.harness_settings)
    clones = run.clones
    assert (clones.tool_dir("builder") / "set_aside" / "01.patch").is_file()
    assert not (clones.path("builder") / "id_rsa").exists()  # ...but the branch is clean
    assert clones.sync_from_main("builder")


async def test_timed_out_turn_gets_one_wrap_up_and_keeps_its_report(tmp_path: Path,
                                                                     fake_llm: FakeLLM) -> None:
    import time
    runner = _runner(tmp_path, [AgentConfig(role="builder")], fake_llm)
    runner.harness_settings.turn_timeout = 4

    def slow(_body: dict[str, Any]) -> ToolCall:
        time.sleep(6)   # longer than the turn timeout: the session is aborted
        return ToolCall("read", {"filePath": "notes.md"})

    fake_llm.route("Time is up for this turn",
                   ToolCall("guudflow_submit_result", {
                       "status": "failed",
                       "summary": "Wrote notes.md with the outline; the parser is still missing."}))
    fake_llm.route("Your turn: builder",
                   ToolCall("write", {"filePath": "notes.md", "content": "# outline\n"}),
                   slow)
    result = await runner.run()
    assert "builder" in result.failed
    results = [s for s in runner.workspace.board
               if s.type is SignalType.AGENT_RESULT and s.from_agent == "builder"]
    assert results and "parser is still missing" in results[-1].summary
    assert (runner.git.working_dir / "notes.md").exists()   # the partial work was kept
