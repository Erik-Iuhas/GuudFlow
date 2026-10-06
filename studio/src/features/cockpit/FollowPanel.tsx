import { cn } from "../../lib/utils";
import { checkProgress, useSpecSlice } from "../spec/api";

/** "What the team must follow": requirements in play, decisions and rules for this run (plan §6). */
export function FollowPanel({ runId, pollMs = 10_000 }: { runId: string; pollMs?: number }) {
  const slice = useSpecSlice(runId, pollMs);
  const data = slice.data;
  const requirements = data?.requirements ?? [];
  const decisions = data?.decisions ?? [];
  const rules = data?.rules ?? [];
  if (!data || (requirements.length === 0 && rules.length === 0)) return null;
  return (
    <aside
      aria-label="What the team must follow"
      className="w-full shrink-0 rounded-lg border border-border bg-card p-3 text-card-foreground lg:w-72"
      data-testid="follow-panel"
    >
      <h3 className="m-0 mb-2 text-sm font-semibold">What the team must follow</h3>
      {requirements.length > 0 && (
        <section aria-label="Requirements in play">
          <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Requirements</h4>
          <ul className="m-0 mb-2 list-none p-0 text-xs">
            {requirements.map((r) => {
              const p = checkProgress(r, data);
              return (
                <li key={r.id} className="flex justify-between gap-2">
                  <span className="min-w-0"><span className="font-mono">{r.id}</span> {r.text}</span>
                  <span className="shrink-0 text-muted-foreground">{p.met}/{p.total}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {decisions.length > 0 && (
        <section aria-label="Decisions">
          <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Decisions</h4>
          <ul className="m-0 mb-2 list-none p-0 text-xs">
            {decisions.map((d) => (
              <li key={d.id}><span className="font-mono">{d.id}</span> {d.title}: {d.choice}</li>
            ))}
          </ul>
        </section>
      )}
      {rules.length > 0 && (
        <section aria-label="Rules">
          <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Rules</h4>
          <ul className="m-0 mb-2 list-none p-0 text-xs">
            {rules.map((r) => {
              const unmet = r.verdicts?.unmet ?? 0;
              return (
                <li key={r.id} className="flex gap-1.5">
                  <span
                    className={cn("shrink-0", unmet > 0 ? "text-status-failed" : "text-status-complete")}
                    aria-label={unmet > 0 ? "rule flagged" : "rule met"}
                  >
                    {unmet > 0 ? "!" : "✓"}
                  </span>
                  <span>{r.text}{unmet > 0 ? ` (${unmet} unmet)` : ""}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      <p className="m-0 text-[11px] text-muted-foreground">
        A flagged rule goes back to the builder as a review issue. If it repeats, the overseer steps in.
      </p>
    </aside>
  );
}
