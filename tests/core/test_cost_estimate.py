"""Tests for core/cost_estimate.py (Phase C.0.5)."""

from __future__ import annotations

from core.cost_estimate import estimate_run_cost
from core.runner import AgentConfig, ProjectConfig


def _project(**overrides) -> ProjectConfig:
    base = dict(
        project_id="p1",
        goal="x",
        agents=[],
        rbac={},
        max_iterations=3,
    )
    base.update(overrides)
    return ProjectConfig(**base)


def test_zero_agents_estimate_is_zero():
    proj = _project(agents=[])
    estimate = estimate_run_cost(proj)
    assert estimate.total_usd == 0.0
    assert estimate.by_role == {}


def test_estimate_includes_each_agent():
    proj = _project(agents=[
        AgentConfig(role="dev", model_tier="solid"),
        AgentConfig(role="reviewer", model_tier="solid"),
    ])
    estimate = estimate_run_cost(proj)
    assert "dev" in estimate.by_role
    assert "reviewer" in estimate.by_role
    assert estimate.total_usd > 0


def test_heavy_tier_costs_more_than_solid():
    """Sanity: HEAVY tier should price out higher per call than SOLID."""
    heavy = _project(agents=[AgentConfig(role="x", model_tier="heavy")])
    solid = _project(agents=[AgentConfig(role="x", model_tier="solid")])
    fast = _project(agents=[AgentConfig(role="x", model_tier="fast")])

    eh = estimate_run_cost(heavy).total_usd
    es = estimate_run_cost(solid).total_usd
    ef = estimate_run_cost(fast).total_usd

    assert eh > es > ef


def test_more_iterations_cost_more():
    """Doubling max_iterations roughly doubles the worst-case estimate."""
    one = _project(
        agents=[AgentConfig(role="x", model_tier="solid")],
        max_iterations=1,
    )
    five = _project(
        agents=[AgentConfig(role="x", model_tier="solid")],
        max_iterations=5,
    )
    estimate_one = estimate_run_cost(one).total_usd
    estimate_five = estimate_run_cost(five).total_usd
    # Within a factor of ~5, allowing rounding.
    assert 4.5 * estimate_one <= estimate_five <= 5.5 * estimate_one


def test_estimate_to_dict_serialisable():
    proj = _project(agents=[AgentConfig(role="x", model_tier="solid")])
    d = estimate_run_cost(proj).to_dict()
    assert isinstance(d["total_usd"], float)
    assert isinstance(d["by_role"], dict)
    assert isinstance(d["notes"], list)


def test_unknown_tier_falls_back_to_solid():
    """Defensive: an unknown model_tier shouldn't crash."""
    proj = _project(agents=[AgentConfig(role="x", model_tier="unknown_tier")])
    estimate = estimate_run_cost(proj)
    assert estimate.total_usd > 0
