"""Cost estimator for runs (Phase C.0.5).

A deterministic, server-side estimator that mirrors the studio's
existing pre-run cost strip math. Used by the `pre_run_cost_preview`
approval gate to surface a $-amount before the user clicks Run.

Cheap and conservative — uses a flat tokens-per-call estimate per
model tier, multiplied by the worst-case loop count
(max_tool_iterations × max_iterations). Real spend will usually come
in lower; the gate exists to catch obvious over-spends, not to be
exact.

If you find this drifting from real costs, adjust the per-tier rates
below — they're meant to be opinionated defaults, not a billing
oracle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.runner import ProjectConfig

# Conservative tokens-per-call estimates (input + output combined)
# per model tier for the fallback path when no registry is available.
_TOKENS_PER_CALL: dict[str, int] = {
    "heavy": 8000,
    "solid": 4000,
    "fast": 1500,
}

# Assumed output fraction for blended rate (output tokens cost more).
_OUTPUT_FRACTION = 0.25

log = logging.getLogger(__name__)


@dataclass
class CostEstimate:
    total_usd: float
    by_role: dict[str, float]
    notes: list[str]

    def to_dict(self) -> dict:
        return {
            "total_usd": round(self.total_usd, 4),
            "by_role": {r: round(v, 4) for r, v in self.by_role.items()},
            "notes": list(self.notes),
        }


def _blended_rate_from_registry(tier: str) -> float | None:
    """Look up blended $/1K-token rate for a tier from models.json.

    Returns None if the registry is unavailable or has no model for the tier.
    """
    try:
        from core import storage
        from core.llm.registry import ModelRegistry
        # The install's tier choice and prices (Studio "Models & providers").
        # We don't need an API key for estimation — just the price fields.
        m = ModelRegistry.for_data_root(storage.data_root()).tier_model(tier)
    except Exception:  # noqa: BLE001 — no registry means no estimate, not a failure
        log.warning("cost estimate: model registry unavailable", exc_info=True)
        return None
    if m is None:
        return None
    # Blended: 75% input tokens, 25% output tokens (conservative output mix)
    return m.cost_per_1k_input * (1 - _OUTPUT_FRACTION) + m.cost_per_1k_output * _OUTPUT_FRACTION


def estimate_run_cost(project: ProjectConfig) -> CostEstimate:
    """Return a $ estimate for one full run of `project` at worst-case
    iteration counts. Always returns a non-negative number."""
    by_role: dict[str, float] = {}
    notes: list[str] = []

    if not project.agents:
        notes.append("project has no agents — estimate is $0")
        return CostEstimate(total_usd=0.0, by_role=by_role, notes=notes)

    max_iter = max(int(project.max_iterations), 1)
    used_registry = False
    for cfg in project.agents:
        tier = cfg.model_tier or "solid"
        tokens_per_call = _TOKENS_PER_CALL.get(tier, _TOKENS_PER_CALL["solid"])
        max_tool_iter = max(int(cfg.max_tool_iterations or 10), 1)
        worst_case_calls = max_iter * max_tool_iter
        worst_case_tokens = worst_case_calls * tokens_per_call

        usd_per_1k = _blended_rate_from_registry(tier)
        if usd_per_1k is not None:
            used_registry = True
        else:
            # Static fallback rates (conservative Anthropic blends)
            usd_per_1k = {"heavy": 0.045, "solid": 0.009, "fast": 0.003}.get(tier, 0.009)

        by_role[cfg.role] = (worst_case_tokens / 1000.0) * usd_per_1k

    total = sum(by_role.values())
    source = "registry" if used_registry else "static fallback"
    notes.append(
        f"Worst-case estimate ({source}): {len(project.agents)} agents × "
        f"max_iterations={max_iter}. Real spend usually lower."
    )
    return CostEstimate(total_usd=total, by_role=by_role, notes=notes)
