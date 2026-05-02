"""Track 12 — runner's sub-LLM resolver priority chain.

Order:
  1. AgentConfig.sub_llm_models_override[use_case]    (per-agent)
  2. ProjectConfig.sub_llm_models[use_case]           (per-project)
  3. ModelRegistry.cheapest_tool_capable()            (registry fallback)
  4. agent._llm + agent._model                        (no-registry dev mode)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import MockLLMClient, MockTurn
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC


def _write_registry(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "models": [
            {
                "id": "model-cheap", "provider": "anthropic", "tier": "fast",
                "supports_tools": True,
                "cost_per_1k_input": 0.0001, "cost_per_1k_output": 0.0001,
            },
            {
                "id": "model-mid", "provider": "anthropic", "tier": "solid",
                "supports_tools": True,
                "cost_per_1k_input": 0.003, "cost_per_1k_output": 0.015,
            },
            {
                "id": "model-heavy", "provider": "anthropic", "tier": "heavy",
                "supports_tools": True,
                "cost_per_1k_input": 0.015, "cost_per_1k_output": 0.075,
            },
        ],
        "providers": {
            "anthropic": {"client": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        },
    }), encoding="utf-8")
    return path


def _make_runner(
    tmp_path: Path,
    monkeypatch,
    *,
    project_overrides: dict[str, str] | None = None,
    agent_overrides: dict[str, str] | None = None,
) -> AgentRunner:
    from core.llm.registry import ModelRegistry
    reg_path = _write_registry(tmp_path, monkeypatch)
    reg = ModelRegistry(reg_path)

    cfg = AgentConfig(
        role="dev",
        description="d",
        depends_on=[],
        sub_llm_models_override=agent_overrides or {},
    )
    proj = ProjectConfig(
        project_id="p",
        goal="g",
        agents=[cfg],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=["llm-dispatch"])},
        sub_llm_models=project_overrides or {},
    )
    runner = AgentRunner(
        project=proj, run_id="r1",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._model_registry = reg  # type: ignore[attr-defined]
    return runner


def _resolver_for(runner: AgentRunner):
    """Pull out the resolver baked into the agent's local tools."""
    tools = runner._instantiate_local_tools("dev")
    return tools["llm-dispatch"]._resolver  # type: ignore[attr-defined]


def test_resolver_falls_back_to_cheapest(tmp_path: Path, monkeypatch):
    runner = _make_runner(tmp_path, monkeypatch)
    resolve = _resolver_for(runner)
    _client, model_id = resolve("llm-dispatch")
    assert model_id == "model-cheap"


def test_resolver_uses_project_override(tmp_path: Path, monkeypatch):
    runner = _make_runner(
        tmp_path, monkeypatch,
        project_overrides={"llm-dispatch": "model-mid"},
    )
    resolve = _resolver_for(runner)
    _client, model_id = resolve("llm-dispatch")
    assert model_id == "model-mid"


def test_resolver_per_agent_override_beats_project(tmp_path: Path, monkeypatch):
    runner = _make_runner(
        tmp_path, monkeypatch,
        project_overrides={"llm-dispatch": "model-mid"},
        agent_overrides={"llm-dispatch": "model-heavy"},
    )
    resolve = _resolver_for(runner)
    _client, model_id = resolve("llm-dispatch")
    assert model_id == "model-heavy"


def test_resolver_unknown_override_falls_back_to_cheapest(tmp_path: Path, monkeypatch):
    """A bogus model id at runtime (e.g. a registry that lost an entry
    after settings load) should NOT crash — the resolver falls back to
    cheapest_tool_capable() rather than raising."""
    runner = _make_runner(
        tmp_path, monkeypatch,
        agent_overrides={"llm-dispatch": "model-does-not-exist"},
    )
    resolve = _resolver_for(runner)
    _client, model_id = resolve("llm-dispatch")
    assert model_id == "model-cheap"


def test_resolver_distinguishes_use_cases(tmp_path: Path, monkeypatch):
    """Different use cases can hit different overrides."""
    runner = _make_runner(
        tmp_path, monkeypatch,
        project_overrides={
            "summarise-subtask": "model-cheap",
            "protocol-cleanup": "model-mid",
        },
        agent_overrides={"llm-dispatch": "model-heavy"},
    )
    tools = runner._instantiate_local_tools("dev")
    summarise_resolver = tools["summarise-subtask"]._resolver
    cleanup_resolver = tools["protocol-cleanup"]._resolver
    dispatch_resolver = tools["llm-dispatch"]._resolver

    assert summarise_resolver("summarise-subtask")[1] == "model-cheap"
    assert cleanup_resolver("protocol-cleanup")[1] == "model-mid"
    assert dispatch_resolver("llm-dispatch")[1] == "model-heavy"
