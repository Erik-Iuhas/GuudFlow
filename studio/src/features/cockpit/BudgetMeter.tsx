import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { cn } from "../../lib/utils";
import { useRunStore } from "../../stores/runStoreContext";
import { computeBudget, formatUsd, type BudgetLevel } from "./model";

const LEVEL_TEXT: Record<BudgetLevel, string> = {
  none: "no cap set",
  ok: "within budget",
  warn: "nearing the cap",
  over: "over budget",
};
// A div bar rather than <progress>: Chrome ignores accent-color there, so the fill
// stayed green while the label said "nearing the cap".
const LEVEL_COLOR: Record<BudgetLevel, string> = {
  none: "bg-primary",
  ok: "bg-status-running",
  warn: "bg-status-blocked",
  over: "bg-status-failed",
};
const LEVEL_TEXT_COLOR: Record<BudgetLevel, string> = {
  none: "text-muted-foreground",
  ok: "text-status-running",
  warn: "text-status-blocked",
  over: "text-status-failed",
};

/**
 * Spend so far against the run's cap (`token_budget_usd`). Hover or focus lists the
 * per-agent breakdown. Without a cap it shows spend only.
 */
export function BudgetMeter({ budgetUsd }: { budgetUsd?: number | null }) {
  const costs = useRunStore((s) => s.costs);
  const b = computeBudget(costs, budgetUsd);
  const pct = b.fraction === null ? null : Math.round(b.fraction * 100);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          tabIndex={0}
          role="group"
          aria-label="Budget"
          data-testid="budget-meter"
          data-level={b.level}
          className="flex min-w-[12rem] flex-col gap-1 rounded-md px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-semibold">
              {formatUsd(b.spent)}
              {b.cap !== null && <span className="font-normal text-muted-foreground"> / {formatUsd(b.cap)}</span>}
            </span>
            <span className={cn("font-medium", LEVEL_TEXT_COLOR[b.level])}>
              {pct !== null ? `${pct}% · ` : ""}
              {LEVEL_TEXT[b.level]}
            </span>
          </div>
          {b.cap !== null && (
            <div
              role="progressbar"
              className="h-2 w-full overflow-hidden rounded-full bg-muted"
              aria-label="Budget spent"
              aria-valuemin={0}
              aria-valuemax={b.cap}
              aria-valuenow={Math.min(b.spent, b.cap)}
              aria-valuetext={`${formatUsd(b.spent)} of ${formatUsd(b.cap)}`}
            >
              {/* Runtime width (exception to the no-inline-style rule). */}
              <div className={cn("h-full rounded-full", LEVEL_COLOR[b.level])} style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
            </div>
          )}
        </div>
      </TooltipTrigger>
      <TooltipContent className="min-w-[12rem]">
        <div className="mb-1 font-semibold">Spend by agent</div>
        {b.perAgent.length === 0 ? (
          <div className="text-muted-foreground">No cost reported yet.</div>
        ) : (
          <table className="w-full border-collapse" data-testid="budget-breakdown">
            <tbody>
              {b.perAgent.map((a) => (
                <tr key={a.role}>
                  <td className="pr-3 font-mono">{a.role}</td>
                  <td className="pr-3 text-right">{formatUsd(a.cost)}</td>
                  <td className="text-right text-muted-foreground">{Math.round(a.share * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {b.remaining !== null && <div className="mt-1 text-muted-foreground">{formatUsd(b.remaining)} remaining</div>}
      </TooltipContent>
    </Tooltip>
  );
}
