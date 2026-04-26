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

from dataclasses import dataclass

from core.runner import ProjectConfig


# Conservative tokens-per-call estimates (input + output combined)
# per model tier. These get multiplied by the per-agent worst-case
# call count to produce a $ figure.
_TOKENS_PER_CALL: dict[str, int] = {
    "heavy": 8000,
    "solid": 4000,
    "fast": 1500,
}


# Approximate $/1K-token blended rates per tier (input × output mix
# weighted toward output). These are deliberately rough — they exist
# to flag "this run might cost $20" vs "this run might cost $0.10".
_USD_PER_1K_TOKENS: dict[str, float] = {
    "heavy": 0.045,   # Opus blend
    "solid": 0.009,   # Sonnet blend
    "fast": 0.003,    # Haiku blend
}


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


def estimate_run_cost(project: ProjectConfig) -> CostEstimate:
    """Return a $ estimate for one full run of `project` at worst-case
    iteration counts. Always returns a non-negative number."""
    by_role: dict[str, float] = {}
    notes: list[str] = []

    if not project.agents:
        notes.append("project has no agents — estimate is $0")
        return CostEstimate(total_usd=0.0, by_role=by_role, notes=notes)

    max_iter = max(int(project.max_iterations), 1)
    for cfg in project.agents:
        tier = cfg.model_tier or "solid"
        tokens_per_call = _TOKENS_PER_CALL.get(tier, _TOKENS_PER_CALL["solid"])
        usd_per_1k = _USD_PER_1K_TOKENS.get(tier, _USD_PER_1K_TOKENS["solid"])
        max_tool_iter = max(int(cfg.max_tool_iterations or 10), 1)

        # Worst case: agent respawns max_iter times, each respawn runs
        # the tool-use loop up to max_tool_iter times.
        worst_case_calls = max_iter * max_tool_iter
        worst_case_tokens = worst_case_calls * tokens_per_call
        role_cost = (worst_case_tokens / 1000.0) * usd_per_1k
        by_role[cfg.role] = role_cost

    total = sum(by_role.values())
    notes.append(
        f"Worst-case estimate: {len(project.agents)} agents × "
        f"max_iterations={max_iter}. Real spend usually lower."
    )
    return CostEstimate(total_usd=total, by_role=by_role, notes=notes)
