import type { CapabilityBlock } from "../lib/capabilities";
import { estimateProjectCost, formatCost, tierClass } from "../lib/cost";
import { cn } from "../lib/utils";
import type { ProjectConfig } from "../types";
import { Card } from "./ui/card";

export function CostStrip({
  project,
  capsByRole,
}: {
  project: ProjectConfig;
  /** Per-role capabilities (opencode harness); scale the estimate. */
  capsByRole?: Record<string, CapabilityBlock | undefined>;
}) {
  const breakdown = estimateProjectCost(project, capsByRole);
  return (
    <Card
      className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-sm"
      data-testid="cost-strip"
    >
      <div className="text-sm font-semibold">~ {formatCost(breakdown.total)} per run (estimate)</div>
      {breakdown.perAgent.map((a) => (
        <span key={a.role} className={cn("text-xs", tierClass(a.tier))}>
          {a.role} ({a.tier}, {a.iterations}×) {formatCost(a.cost)}
        </span>
      ))}
      <span className="ml-auto text-xs text-muted-foreground">
        Estimate based on tier tokens × iterations × indicative model pricing.
      </span>
    </Card>
  );
}
