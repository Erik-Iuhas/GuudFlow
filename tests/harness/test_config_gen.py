"""Tests for core.harness.config_gen: unit shape checks plus two real-opencode boots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from core.harness.capabilities import (
    READONLY_BASH, CompiledCapabilities, compile_capabilities, defaults_for,
)
from core.harness.config_gen import (
    EXPLORE_OVERRIDE, ImageCaps, ProviderBinding, agent_entry, build_config, provider_entry,
)
from core.harness.permissions import build_permission
from core.harness.server import OpencodeServer
from core.llm.registry import ModelInfo
from tests.harness.conftest import init_repo, requires_opencode
from tests.harness.fake_llm import FakeLLM, fake_provider_config

CLAUDE = ModelInfo(id="claude-x", provider="anthropic", tier="heavy", supports_images=True,
                   max_context_tokens=200000, max_output_tokens=16000)
MISTRAL = ModelInfo(id="mistral-small", provider="mistral", tier="fast", supports_images=False,
                    max_context_tokens=32000, max_output_tokens=4096)
ANTHROPIC = ProviderBinding("anthropic", CLAUDE, "anthropic")


def _compiled(role: str = "builder") -> CompiledCapabilities:
    return compile_capabilities(defaults_for(role), role)


def _walk_keys(node: Any) -> list[str]:  # noqa: ANN401 - arbitrary JSON tree
    if isinstance(node, dict):
        return [str(k) for k in node] + [k for v in node.values() for k in _walk_keys(v)]
    if isinstance(node, list):
        return [k for v in node for k in _walk_keys(v)]
    return []


def test_provider_entry_anthropic() -> None:
    entry = provider_entry(ANTHROPIC)
    assert entry["npm"] == "@ai-sdk/anthropic"
    assert entry["options"] == {}
    assert entry["models"]["claude-x"] == {
        "name": "claude-x", "tool_call": True, "attachment": True,
        "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
        "limit": {"context": 200000, "input": 200000, "output": 16000},
    }


def test_working_context_keeps_a_big_window_cheap() -> None:
    big = ModelInfo(id="claude-big", provider="anthropic", tier="solid",
                    max_context_tokens=1_000_000, max_output_tokens=128000)
    limit = provider_entry(ProviderBinding("anthropic", big, "anthropic"))["models"]["claude-big"]["limit"]
    assert limit == {"context": 1_000_000, "input": 200_000, "output": 128000}   # the default
    tuned = ModelInfo(id="claude-big", provider="anthropic", tier="solid",
                      max_context_tokens=1_000_000, working_context_tokens=400_000)
    assert provider_entry(ProviderBinding("anthropic", tuned, "anthropic"))[
        "models"]["claude-big"]["limit"]["input"] == 400_000
    project = ProviderBinding("anthropic", tuned, "anthropic", working_context=120_000)
    assert provider_entry(project)["models"]["claude-big"]["limit"]["input"] == 120_000


def test_provider_entry_openai_compatible_options() -> None:
    entry = provider_entry(ProviderBinding("mistral", MISTRAL, "openai_compatible",
                                           base_url="http://gw/v1", api_key="k"))
    assert entry["npm"] == "@ai-sdk/openai-compatible"
    assert entry["options"] == {"baseURL": "http://gw/v1", "apiKey": "k"}
    model = entry["models"]["mistral-small"]
    assert model["attachment"] is False
    assert model["modalities"]["input"] == ["text"]
    assert model["limit"] == {"context": 32000, "input": 32000, "output": 4096}
    only_url = provider_entry(ProviderBinding("mistral", MISTRAL, "openai_compatible",
                                              base_url="http://gw/v1"))
    assert only_url["options"] == {"baseURL": "http://gw/v1"}


def test_provider_entry_rejects_unknown_client() -> None:
    with pytest.raises(ValueError):
        provider_entry(ProviderBinding("x", CLAUDE, "bogus"))


def test_agent_entry_uses_build_permission_unchanged() -> None:
    compiled = _compiled()
    entry = agent_entry(compiled, model_ref="anthropic/claude-x", prompt="p", description="d")
    assert entry["mode"] == "primary" and entry["model"] == "anthropic/claude-x"
    assert entry["steps"] == 60 and "temperature" not in entry
    assert entry["permission"] == build_permission(compiled)
    assert list(entry["permission"]) == list(build_permission(compiled))
    assert agent_entry(compiled, model_ref="m", prompt="p", temperature=0.2)["temperature"] == 0.2


def test_explore_override_is_read_only() -> None:
    perm = EXPLORE_OVERRIDE["permission"]
    assert perm["task"] == "deny" and perm["edit"] == "deny"
    assert perm["bash"]["*"] == "deny"
    assert all(perm["bash"][p] == "allow" for p in READONLY_BASH)


def test_build_config_shape_and_order() -> None:
    agent = agent_entry(_compiled(), model_ref=ANTHROPIC.model_ref, prompt="p")
    config = build_config(role="builder", agent=agent, binding=ANTHROPIC)
    assert list(config) == ["$schema", "share", "autoupdate", "experimental", "model", "small_model",
                            "default_agent", "provider", "permission", "agent",
                            "subagent_depth", "compaction", "attachment"]
    assert config["share"] == "disabled" and config["autoupdate"] is False
    assert config["model"] == config["small_model"] == "anthropic/claude-x"
    assert config["default_agent"] == "builder"
    assert config["permission"] == {"*": "deny", "read": "allow", "glob": "allow",
                                    "grep": "allow", "list": "allow"}
    assert list(config["agent"]) == ["builder", "explore"]
    assert config["agent"]["explore"] == EXPLORE_OVERRIDE
    assert config["subagent_depth"] == 1 and config["compaction"] == {"auto": True, "prune": True}
    assert config["attachment"]["image"] == {
        "auto_resize": True, "max_width": 1280, "max_height": 1280, "max_base64_bytes": 5_000_000}
    assert "mcp" not in config
    assert "format" not in _walk_keys(config)


def test_build_config_image_caps_and_extra_providers() -> None:
    config = build_config(role="r", agent={}, binding=ANTHROPIC,
                          image_caps=ImageCaps(640, 480, 1000),
                          extra_providers={"fake": {"npm": "x"}})
    assert config["attachment"]["image"]["max_width"] == 640
    assert config["attachment"]["image"]["max_base64_bytes"] == 1000
    assert list(config["provider"]) == ["anthropic", "fake"]


def test_small_binding_same_provider_merges_models() -> None:
    small_model = ModelInfo(id="claude-small", provider="anthropic", tier="fast")
    config = build_config(role="r", agent={}, binding=ANTHROPIC,
                          small_binding=ProviderBinding("anthropic", small_model, "anthropic"))
    assert list(config["provider"]) == ["anthropic"]
    assert set(config["provider"]["anthropic"]["models"]) == {"claude-x", "claude-small"}
    assert config["small_model"] == "anthropic/claude-small"


def test_small_binding_other_provider_gets_own_entry() -> None:
    config = build_config(role="r", agent={}, binding=ANTHROPIC,
                          small_binding=ProviderBinding("mistral", MISTRAL, "openai_compatible"))
    assert list(config["provider"]) == ["anthropic", "mistral"]
    assert config["small_model"] == "mistral/mistral-small"


def test_mcp_block() -> None:
    config = build_config(role="r", agent={}, binding=ANTHROPIC,
                          mcp_url="http://127.0.0.1:9/mcp", mcp_token="tok")
    assert config["mcp"] == {"guudflow": {
        "type": "remote", "url": "http://127.0.0.1:9/mcp", "enabled": True, "oauth": False,
        "headers": {"Authorization": "Bearer tok"}}}
    assert "format" not in _walk_keys(config)


def test_mcp_url_without_token_raises() -> None:
    with pytest.raises(ValueError):
        build_config(role="r", agent={}, binding=ANTHROPIC, mcp_url="http://127.0.0.1:9/mcp")


async def _boot_and_list(role: str, tmp_path: Path,
                         fake: FakeLLM) -> tuple[dict[str, dict[str, Any]], int]:
    """Boot opencode with a generated config for `role`; return /agent by name and /config status."""
    fake_model = ModelInfo(id="fake-model", provider="fake", tier="fast", supports_images=True,
                           max_output_tokens=8000)
    binding = ProviderBinding("fake", fake_model, "openai_compatible")
    agent = agent_entry(_compiled(role), model_ref=binding.model_ref, prompt=f"You are the {role}.")
    config = build_config(role=role, agent=agent, binding=binding,
                          extra_providers=fake_provider_config(fake.base_url)["provider"])
    workdir = init_repo(tmp_path / "work")
    server = OpencodeServer(config=config, home=tmp_path / "home", workdir=workdir)
    await server.start()
    try:
        auth = httpx.BasicAuth("opencode", server.password)
        async with httpx.AsyncClient(auth=auth, base_url=server.base_url, timeout=10.0) as http:
            params = {"directory": str(workdir)}
            agents = await http.get("/agent", params=params)
            agents.raise_for_status()
            cfg = await http.get("/config", params=params)
        return {a["name"]: a for a in agents.json()}, cfg.status_code
    finally:
        await server.stop()


@requires_opencode
async def test_builder_agent_is_locked_in_real_opencode(tmp_path: Path, fake_llm: FakeLLM) -> None:
    agents, _ = await _boot_and_list("builder", tmp_path, fake_llm)
    builder = agents["builder"]
    assert builder["mode"] == "primary"
    assert {"permission": "bash", "pattern": "git push*", "action": "deny"} in builder["permission"]
    task_rules = [r for r in agents["explore"]["permission"] if r["permission"] == "task"]
    assert task_rules and task_rules[-1]["action"] == "deny"


@requires_opencode
async def test_reviewer_config_accepted_by_real_opencode(tmp_path: Path, fake_llm: FakeLLM) -> None:
    agents, status = await _boot_and_list("reviewer", tmp_path, fake_llm)
    assert status == 200
    assert "reviewer" in agents


def test_project_can_only_lower_the_working_context() -> None:
    from types import SimpleNamespace

    from core.harness.run import OpencodeRun

    def cap(gate: object, model_cap: int = 200_000) -> int:
        fake = SimpleNamespace(runner=SimpleNamespace(project=SimpleNamespace(
            approval_gates={"working_context_tokens": gate})))
        return OpencodeRun._working_context(fake, model_cap)  # type: ignore[arg-type]

    assert cap(None) == 200_000 and cap("") == 200_000
    assert cap(80_000) == 80_000 and cap(500_000) == 200_000
    assert cap("lots") == 200_000


def test_effort_becomes_the_providers_own_option() -> None:
    m = ModelInfo(id="m1", provider="anthropic", tier="solid", effort_levels=("low", "high"))
    entry = provider_entry(ProviderBinding("anthropic", m, "anthropic", effort="high"))
    assert entry["models"]["m1"]["options"] == {"effort": "high"}
    oai = provider_entry(ProviderBinding("openai", m, "openai_compatible",
                                         base_url="https://x/v1", effort="low"))
    assert oai["models"]["m1"]["options"] == {"reasoningEffort": "low"}
    assert "options" not in provider_entry(ProviderBinding("anthropic", m, "anthropic"))["models"]["m1"]


def test_role_effort_then_project_gate_then_model_default() -> None:
    from types import SimpleNamespace

    from core.harness.run import OpencodeRun

    model = ModelInfo(id="m", provider="anthropic", tier="solid",
                      effort_levels=("low", "medium", "high"), default_effort="medium")

    def effort(role: str, gate: object) -> str | None:
        fake = SimpleNamespace(runner=SimpleNamespace(project=SimpleNamespace(
            approval_gates={"effort": gate} if gate is not None else {})))
        return OpencodeRun._effort(fake, SimpleNamespace(effort=role), model)  # type: ignore[arg-type]

    assert effort("", None) == "medium"
    assert effort("", "low") == "low"
    assert effort("high", "low") == "high"
    assert effort("max", None) == "high"   # fitted to the model


@requires_opencode
async def test_real_opencode_sends_the_effort_level(tmp_path: Path, fake_llm: FakeLLM) -> None:
    from core.harness.opencode_client import OpencodeClient, text_part
    from tests.harness.fake_llm import Text

    fake_llm.script(Text("ok"))
    model = ModelInfo(id="fake-model", provider="fake", tier="fast", max_output_tokens=8000,
                      effort_levels=("low", "high"))
    binding = ProviderBinding("fake", model, "openai_compatible", base_url=fake_llm.base_url,
                              api_key="fake-key", effort="high")
    agent = agent_entry(_compiled("builder"), model_ref=binding.model_ref, prompt="You build.")
    config = build_config(role="builder", agent=agent, binding=binding)
    workdir = init_repo(tmp_path / "work")
    server = OpencodeServer(config=config, home=tmp_path / "home", workdir=workdir)
    await server.start()
    try:
        client = OpencodeClient(server.base_url, str(workdir), password=server.password,
                                timeout=60.0)
        session = await client.create_session()
        await client.prompt(session["id"], [text_part("hi")], agent="builder")
        await client.aclose()
    finally:
        await server.stop()
    steps = fake_llm.tool_requests()
    assert steps and all(r["reasoning_effort"] == "high" for r in steps)


def test_effort_is_sent_in_the_providers_own_words() -> None:
    from pathlib import Path as _P

    from core.llm.registry import ModelRegistry
    small = ModelRegistry(_P(__file__).resolve().parents[2] / "data" / "models.json").get(
        "mistral-small-latest")
    assert small is not None
    level = small.pick_effort("medium")          # Mistral takes "none" or "high": medium -> low
    entry = provider_entry(ProviderBinding("mistral", small, "openai_compatible",
                                           base_url="https://x/v1", effort=level))
    assert entry["models"]["mistral-small-latest"]["options"] == {"reasoningEffort": "none"}
    assert small.effort_value(small.pick_effort("max")) == "high"


@requires_opencode
async def test_real_opencode_takes_a_per_turn_effort_variant(tmp_path: Path, fake_llm: FakeLLM) -> None:
    from core.harness.opencode_client import OpencodeClient, text_part
    from tests.harness.fake_llm import Text

    fake_llm.script(Text("one"), Text("two"))
    model = ModelInfo(id="fake-model", provider="fake", tier="fast", max_output_tokens=8000,
                      effort_levels=("low", "high"), effort_values={"low": "none"})
    binding = ProviderBinding("fake", model, "openai_compatible", base_url=fake_llm.base_url,
                              api_key="fake-key", effort="high")
    agent = agent_entry(_compiled("builder"), model_ref=binding.model_ref, prompt="You build.")
    config = build_config(role="builder", agent=agent, binding=binding)
    workdir = init_repo(tmp_path / "work")
    server = OpencodeServer(config=config, home=tmp_path / "home", workdir=workdir)
    await server.start()
    try:
        client = OpencodeClient(server.base_url, str(workdir), password=server.password,
                                timeout=60.0)
        first = await client.create_session()
        await client.prompt(first["id"], [text_part("hi")], agent="builder")
        second = await client.create_session()
        await client.prompt(second["id"], [text_part("easy one")], agent="builder", variant="low")
        await client.aclose()
    finally:
        await server.stop()
    efforts = [r["reasoning_effort"] for r in fake_llm.tool_requests()]
    assert efforts[0] == "high" and efforts[-1] == "none", efforts


def test_every_effort_level_is_a_variant() -> None:
    m = ModelInfo(id="m1", provider="mistral", tier="fast", effort_levels=("low", "high"),
                  effort_values={"low": "none"})
    entry = provider_entry(ProviderBinding("mistral", m, "openai_compatible", base_url="https://x/v1"))
    assert entry["models"]["m1"]["variants"] == {"low": {"reasoningEffort": "none"},
                                                 "high": {"reasoningEffort": "high"}}
    plain = ModelInfo(id="m2", provider="anthropic", tier="fast")
    assert "variants" not in provider_entry(ProviderBinding("anthropic", plain, "anthropic"))[
        "models"]["m2"]


def test_a_cards_item_effort_becomes_the_turn_variant() -> None:
    from types import SimpleNamespace

    from core.harness.run import OpencodeRun
    from core.harness.task_flow import TurnPlan
    from core.harness.results import AgentResult

    model = ModelInfo(id="m", provider="anthropic", tier="solid", effort_levels=("low", "high"))
    role = SimpleNamespace(binding=SimpleNamespace(model=model))
    assert OpencodeRun._turn_variant(role, TurnPlan(model=AgentResult, effort="xhigh")) == "high"
    assert OpencodeRun._turn_variant(role, TurnPlan(model=AgentResult)) is None
    assert OpencodeRun._turn_variant(role, None) is None


def test_telemetry_is_off() -> None:
    """opencode's OpenTelemetry export stays off: config flag and the OTel SDK switch."""
    from core.harness.server import LOCKED_FLAGS
    config = build_config(role="r", agent={}, binding=ANTHROPIC)
    assert config["experimental"]["openTelemetry"] is False and config["share"] == "disabled"
    assert LOCKED_FLAGS["OTEL_SDK_DISABLED"] == "true"
    assert LOCKED_FLAGS["OPENCODE_DISABLE_SHARE"] == "1"
