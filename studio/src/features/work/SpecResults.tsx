import { cn } from "../../lib/utils";
import { checkProgress, useSpecSlice } from "../spec/api";
import type { CheckState } from "../plan/api";

export const CHECK_MARK: Record<CheckState, string> = { met: "✓", unmet: "✕", unknown: "○" };
export const CHECK_TEXT: Record<CheckState, string> = {
  met: "text-status-complete",
  unmet: "text-status-failed",
  unknown: "text-muted-foreground",
};

/** "Against the spec": the served requirements' checks and the rules met or broken (plan §6). */
export function SpecResults({ runId }: { runId: string }) {
  const slice = useSpecSlice(runId);
  const data = slice.data;
  if (!data || ((data.requirements ?? []).length === 0 && (data.rules ?? []).length === 0)) return null;
  const rules = data.rules ?? [];
  return (
    <section className="mt-2 rounded-md border border-border bg-muted/40 p-2" aria-label="Against the spec" data-testid={`spec-results-${runId}`}>
      <h4 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Against the spec</h4>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
        {(data.requirements ?? []).map((r) => {
          const p = checkProgress(r, data);
          return (
            <li key={r.id}>
              <span className="font-mono text-xs">{r.id}</span> {r.text}{" "}
              <span className="text-xs text-muted-foreground">({p.met}/{p.total} checks)</span>
              <ul className="m-0 list-none pl-4 text-xs">
                {(r.checks ?? []).map((c) => {
                  const state = data.checks?.[c.id]?.state ?? c.state;
                  return (
                    <li key={c.id} className="flex gap-1.5">
                      <span className={cn("w-3 shrink-0", CHECK_TEXT[state])} aria-label={state}>{CHECK_MARK[state]}</span>
                      <span className="font-mono">{c.id}</span>
                      <span>{c.text}</span>
                    </li>
                  );
                })}
              </ul>
            </li>
          );
        })}
      </ul>
      {rules.length > 0 && (
        <ul className="m-0 mt-2 flex list-none flex-col gap-0.5 p-0 text-xs">
          {rules.map((r) => {
            const unmet = r.verdicts?.unmet ?? 0;
            return (
              <li key={r.id} className="flex gap-1.5">
                <span className={unmet > 0 ? "text-status-failed" : "text-status-complete"} aria-label={unmet > 0 ? "rule broken" : "rule met"}>
                  {unmet > 0 ? "!" : "✓"}
                </span>
                <span>{r.text}</span>
                {r.verdicts && (
                  <span className="text-muted-foreground">
                    {r.verdicts.met} met{unmet > 0 ? `, ${unmet} unmet` : ""}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
