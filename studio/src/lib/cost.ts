// Live cost estimate — DESIGN §22 / CLAUDE.md "live cost estimate updates
// on every model change". Estimate-only; not real billing.

import type { AgentConfig, ModelTier, ProjectConfig } from "../types";

// Per-tier pricing (USD per 1M tokens). Indicative; treat as ballpark.
const TIER_PRICING: Record<ModelTier, { inputPerM: number; outputPerM: number }> = {
  heavy: { inputPerM: 15.0, outputPerM: 75.0 },   // Opus-class
  solid: { inputPerM: 3.0, outputPerM: 15.0 },    // Sonnet-class
  fast: { inputPerM: 0.8, outputPerM: 4.0 },      // Haiku-class
};

// Estimated tokens per agent per iteration. Comes from §28 budget defaults
// (HEAVY 12k, SOLID 6k, FAST 2k input) plus an output approximation.
const TIER_TOKENS_PER_ITER: Record<ModelTier, { input: number; output: number }> = {
  heavy: { input: 12_000, output: 4_000 },
  solid: { input: 6_000, output: 2_000 },
  fast: { input: 2_000, output: 800 },
};

export interface CostBreakdown {
  total: number;
  perAgent: { role: string; tier: ModelTier; iterations: number; cost: number }[];
}

export function estimateProjectCost(project: ProjectConfig): CostBreakdown {
  const maxIter = project.max_iterations ?? 5;
  const breakdown: CostBreakdown["perAgent"] = [];

  for (const agent of project.agents ?? []) {
    const tier: ModelTier = (agent.model_tier ?? "solid") as ModelTier;
    const iters = agent.max_tool_iterations ?? maxIter;
    const cost = estimateAgentCost(tier, iters);
    breakdown.push({
      role: agent.role,
      tier,
      iterations: iters,
      cost,
    });
  }

  const total = breakdown.reduce((acc, b) => acc + b.cost, 0);
  return { total, perAgent: breakdown };
}

export function estimateAgentCost(tier: ModelTier, iterations: number): number {
  const tokens = TIER_TOKENS_PER_ITER[tier];
  const pricing = TIER_PRICING[tier];
  const inputCost = (tokens.input * iterations * pricing.inputPerM) / 1_000_000;
  const outputCost = (tokens.output * iterations * pricing.outputPerM) / 1_000_000;
  return inputCost + outputCost;
}

export function formatCost(usd: number): string {
  if (usd < 0.01) return "<$0.01";
  if (usd < 1) return `$${usd.toFixed(2)}`;
  return `$${usd.toFixed(2)}`;
}

export function tierColor(tier: ModelTier): string {
  return { heavy: "#a855f7", solid: "#3b82f6", fast: "#10b981" }[tier];
}
