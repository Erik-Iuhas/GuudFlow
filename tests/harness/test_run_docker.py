"""O7: a project run with every role in a hardened Docker sandbox (plan §5.3, §5.4).

opencode runs inside the container; model traffic goes container → gateway (host,
on the run network's gateway IP) → FakeLLM; MCP calls go container → GuudFlow MCP.
Skipped when Docker or the opencode binary is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.harness.config_gen import ProviderBinding
from core.harness.run import HarnessSettings
from core.harness.server import opencode_available
from core.llm.registry import ModelInfo
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.scheduler import SchedulerStatus
from core.signals import SignalType
from tests.harness.fake_llm import FakeLLM, ToolCall

UPSTREAM_SECRET = "UPSTREAM-SECRET-do-not-leak-123"
FAKE_MODEL = ModelInfo(id="fake-model", provider="fake", tier="solid", supports_images=True,
                       cost_per_1k_input=0.001, cost_per_1k_output=0.002)


def _docker_ok() -> bool:
    try:
        from worker.sandbox_manager import SandboxManager
        return SandboxManager.available() and opencode_available()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="docker or opencode unavailable")


def docker_runner(tmp_path: Path, fake: FakeLLM, agents: list[AgentConfig],
                  **settings: Any) -> AgentRunner:
    """An AgentRunner on the opencode harness in docker mode, wired to `fake`."""
    project = ProjectConfig(project_id="p", goal="Build a tiny greeting module.",
                            agents=agents, harness="opencode")
    runner = AgentRunner(project=project, run_id=f"dock{abs(hash(tmp_path)) % 10**8}",
                         run_root=tmp_path / "run", max_wall_seconds=600)
    runner.harness_settings = HarnessSettings(
        production=False, turn_timeout=120, sandbox="docker",
        binding_factory=lambda cfg: ProviderBinding("fake", FAKE_MODEL, "openai_compatible",
                                                    base_url=fake.base_url,
                                                    api_key=UPSTREAM_SECRET),
        **settings)
    return runner


def done(summary: str) -> ToolCall:
    """A valid submit_result call."""
    return ToolCall("guudflow_submit_result", {"status": "done", "summary": summary})


async def test_builder_runs_in_sandbox_end_to_end(tmp_path: Path, fake_llm: FakeLLM) -> None:
    container_envs: list[dict[str, str]] = []

    def capture_env(role: str, event: dict[str, Any]) -> None:
        if event.get("type") == "server.connected" and not container_envs:
            import docker
            client = docker.from_env()
            for c in client.containers.list(filters={"label": "guudflow.role=builder"}):
                env = c.attrs["Config"]["Env"]
                container_envs.append(dict(e.split("=", 1) for e in env))

    fake_llm.route("Your turn: builder",
                   ToolCall("write", {"filePath": "greet.py", "content": "def greet():\n    return 'hi'\n"}),
                   ToolCall("bash", {"command": "python3 -c \"import greet; print(greet.greet())\"",
                                     "description": "run it"}),
                   done("Added greet.py and ran it inside the sandbox."))
    runner = docker_runner(tmp_path, fake_llm, [AgentConfig(role="builder")],
                           on_event=capture_env)
    result = await runner.run()
    assert result.status is SchedulerStatus.COMPLETE, [s.summary for s in runner.workspace.board]
    assert (runner.git.working_dir / "greet.py").exists()

    caps = next(s for s in runner.workspace.board if s.type is SignalType.CAPABILITIES)
    assert json.loads(caps.detail or "{}")["effective"]["sandbox"] == "standard"
    tool_calls = [s for s in runner.workspace.board if s.type is SignalType.TOOL_CALL]
    assert any("bash" in s.summary and "→ ok" in s.summary for s in tool_calls)

    # The real provider key never entered the container; opencode only had a virtual key.
    assert container_envs, "container env was captured"
    flat = json.dumps(container_envs)
    assert UPSTREAM_SECRET not in flat
    assert "OPENCODE_CONFIG_CONTENT" in container_envs[0]

    # Every model call went through the gateway and was costed.
    log = (runner.run_root / "llm_costs.jsonl").read_text().splitlines()
    assert log and all(json.loads(line)["role"] == "builder" for line in log)

    # Nothing left behind.
    import docker
    assert not docker.from_env().containers.list(all=True, filters={"label": f"guudflow.run={runner.run_id}"})
