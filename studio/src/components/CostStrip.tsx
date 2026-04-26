import { estimateProjectCost, formatCost, tierColor } from "../lib/cost";
import type { ProjectConfig } from "../types";

export function CostStrip({ project }: { project: ProjectConfig }) {
  const breakdown = estimateProjectCost(project);
  return (
    <div className="cost-strip">
      <div className="total">~ {formatCost(breakdown.total)} per run (estimate)</div>
      {breakdown.perAgent.map((a) => (
        <span
          key={a.role}
          style={{ color: tierColor(a.tier), fontSize: 12 }}
        >
          {a.role} ({a.tier}, {a.iterations}×) {formatCost(a.cost)}
        </span>
      ))}
      <span className="muted small" style={{ marginLeft: "auto" }}>
        Estimate based on tier tokens × iterations × indicative model pricing.
      </span>
    </div>
  );
}
