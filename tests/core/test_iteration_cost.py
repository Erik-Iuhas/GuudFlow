"""Track 3d.5 — estimated_cost_usd on IterationMetrics.

The runner's `_estimate_iteration_cost` helper combines:
  - Main agent LLM cost (input/output/cache tokens × per-1k pricing)
  - Per-use-case sub-LLM dispatch cost (each priced against its own
    resolver-picked model)

Returns 0.0 silently when no registry / unknown model. Best-effort
observability — never raises from the iteration finalize path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.llm import MockLLMClient
from core.runner import AgentConfig, AgentRunner, ProjectConfig
from core.tool_manifest import RoleRBAC
from core.workspace import IterationMetrics


# ── Registry fixture ──────────────────────────────────────────────────────


def _write_registry(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "models": [
            {
                "id": "model-cheap", "provider": "anthropic", "tier": "fast",
                "supports_tools": True,
                "cost_per_1k_input": 0.001,
                "cost_per_1k_output": 0.002,
                "cache_read_per_1k": 0.0001,
            },
            {
                "id": "model-mid", "provider": "anthropic", "tier": "solid",
                "supports_tools": True,
                "cost_per_1k_input": 0.003,
                "cost_per_1k_output": 0.015,
                "cache_read_per_1k": 0.0003,
            },
        ],
        "providers": {
            "anthropic": {"client": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        },
    }), encoding="utf-8")
    return path


def _make_runner(tmp_path: Path, monkeypatch, *, sub_llm_models=None) -> AgentRunner:
    from core.llm.registry import ModelRegistry
    reg_path = _write_registry(tmp_path, monkeypatch)
    reg = ModelRegistry(reg_path)
    proj = ProjectConfig(
        project_id="p_cost", goal="g",
        agents=[AgentConfig(role="dev", model_tier="solid")],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
        sub_llm_models=sub_llm_models or {},
    )
    runner = AgentRunner(
        project=proj, run_id="r_cost",
        run_root=tmp_path / "run", llm=MockLLMClient([]),
    )
    runner._model_registry = reg  # type: ignore[attr-defined]
    return runner


# ── Main agent cost only ──────────────────────────────────────────────────


def test_cost_zero_when_no_registry(tmp_path: Path) -> None:
    """Single-LLM dev mode: no registry → cost stays 0.0 silently."""
    proj = ProjectConfig(
        project_id="p", goal="g",
        agents=[AgentConfig(role="dev", model_tier="solid")],
        rbac={"dev": RoleRBAC(role="dev", allowed_scopes=[])},
    )
    runner = AgentRunner(
        project=proj, run_id="r", run_root=tmp_path / "run",
        llm=MockLLMClient([]),
    )
    m = IterationMetrics(
        run_id="r", role="dev", iteration=0,
        model="model-mid",
        input_tokens=1000, output_tokens=500,
    )
    assert runner._estimate_iteration_cost(m) == 0.0


def test_cost_main_llm_only(tmp_path: Path, monkeypatch) -> None:
    """input * in_per_1k + output * out_per_1k for the agent's model."""
    runner = _make_runner(tmp_path, monkeypatch)
    m = IterationMetrics(
        run_id="r", role="dev", iteration=0,
        model="model-mid",
        input_tokens=10_000, output_tokens=2_000,
    )
    cost = runner._estimate_iteration_cost(m)
    # 10_000/1000 * $0.003 + 2_000/1000 * $0.015 = 0.03 + 0.03 = $0.06
    assert cost == pytest.approx(0.06, rel=1e-3)


def test_cost_includes_cache_tokens(tmp_path: Path, monkeypatch) -> None:
    runner = _make_runner(tmp_path, monkeypatch)
    m = IterationMetrics(
        run_id="r", role="dev", iteration=0,
        model="model-mid",
        input_tokens=1_000, output_tokens=0,
        cache_read_tokens=10_000,    # × 0.0003 / 1000 = 0.003
        cache_creation_tokens=5_000, # billed at input rate: × 0.003 / 1000 = 0.015
    )
    cost = runner._estimate_iteration_cost(m)
    # 1_000/1000 * $0.003 + 10_000/1000 * $0.0003 + 5_000/1000 * $0.003
    # = 0.003 + 0.003 + 0.015 = $0.021
    assert cost == pytest.approx(0.021, rel=1e-3)


def test_cost_unknown_model_zero_for_main(tmp_path: Path, monkeypatch) -> None:
    """Model id missing from registry → main contribution is zero. Sub-LLM
    contributions still computed if present."""
    runner = _make_runner(tmp_path, monkeypatch)
    m = IterationMetrics(
        run_id="r", role="dev", iteration=0,
        model="not-in-registry",
        input_tokens=10_000, output_tokens=2_000,
    )
    assert runner._estimate_iteration_cost(m) == 0.0


# ── Sub-LLM contributions ─────────────────────────────────────────────────


def test_cost_includes_sub_llm_breakdown_with_project_override(
    tmp_path: Path, monkeypatch,
) -> None:
    runner = _make_runner(
        tmp_path, monkeypatch,
        sub_llm_models={"summarise-subtask": "model-cheap"},
    )
    m = IterationMetrics(
        run_id="r", role="dev", iteration=0,
        model="model-mid",
        input_tokens=1_000, output_tokens=200,
        sub_llm_breakdown={
            "summarise-subtask": {
                "calls": 2, "input_tokens": 500, "output_tokens": 100,
            },
        },
    )
    cost = runner._estimate_iteration_cost(m)
    # Main: 1_000/1000 * $0.003 + 200/1000 * $0.015 = 0.003 + 0.003 = 0.006
    # Sub:  500/1000 * $0.001 + 100/1000 * $0.002 = 0.0005 + 0.0002 = 0.0007
    # Total ≈ 0.0067
    assert cost == pytest.approx(0.0067, rel=1e-3)


def test_cost_sub_llm_falls_back_to_cheapest_when_no_project_override(
    tmp_path: Path, monkeypatch,
) -> None:
    """No `sub_llm_models[use_case]` set → use cheapest tool-capable."""
    runner = _make_runner(tmp_path, monkeypatch)  # empty sub_llm_models
    m = IterationMetrics(
        run_id="r", role="dev", iteration=0,
        model="model-mid",
        input_tokens=0, output_tokens=0,
        sub_llm_breakdown={
            "protocol-cleanup": {
                "calls": 1, "input_tokens": 200, "output_tokens": 50,
            },
        },
    )
    cost = runner._estimate_iteration_cost(m)
    # Cheapest tool-capable = model-cheap (0.001 in / 0.002 out).
    # 200/1000 * 0.001 + 50/1000 * 0.002 = 0.0002 + 0.0001 = $0.0003
    assert cost == pytest.approx(0.0003, rel=1e-3)


# ── Resilience ────────────────────────────────────────────────────────────


def test_cost_never_raises_on_malformed_breakdown(tmp_path: Path, monkeypatch) -> None:
    """Best-effort: a corrupt sub_llm_breakdown entry shouldn't crash
    the iteration finalize path."""
    runner = _make_runner(tmp_path, monkeypatch)
    m = IterationMetrics(
        run_id="r", role="dev", iteration=0,
        model="model-mid",
        input_tokens=1_000, output_tokens=0,
        sub_llm_breakdown={
            "weird": {"calls": "not-a-number", "input_tokens": None},  # type: ignore
        },
    )
    # No exception; main cost still computed.
    cost = runner._estimate_iteration_cost(m)
    assert cost > 0  # main contribution is fine
