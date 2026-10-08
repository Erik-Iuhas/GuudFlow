import { cn } from "../../lib/utils";
import type { CheckState } from "../plan/api";
import { checkProgress, useSpecSlice, type SliceRequirement, type SpecSlice } from "../spec/api";

const CHECK_MARK: Record<string, { icon: string; label: string; cls: string }> = {
  met: { icon: "✓", label: "met", cls: "text-status-complete" },
  unmet: { icon: "✕", label: "unmet", cls: "text-status-failed" },
  unknown: { icon: "○", label: "not checked yet", cls: "text-muted-foreground" },
};

/** A requirement's short name: its title, else its first clause. */
function shortTitle(r: SliceRequirement): string {
  if (r.title?.trim()) return r.title.trim();
  const first = r.text.split(/[.:;]/)[0]?.trim() ?? r.text;
  return first.length > 60 ? `${first.slice(0, 57)}…` : first;
}

function Progress({ met, total }: { met: number; total: number }) {
  const pct = total ? Math.round((met / total) * 100) : 0;
  return (
    <span className="flex shrink-0 items-center gap-1.5" title={`${met} of ${total} checks met`}>
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className={cn("block h-full rounded-full", met === total && total > 0 ? "bg-status-complete" : "bg-primary")}
          style={{ width: `${pct}%` }} />
      </span>
      <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{met}/{total}</span>
    </span>
  );
}

function RequirementRow({ r, data }: { r: SliceRequirement; data: SpecSlice }) {
  const p = checkProgress(r, data);
  return (
    <li className="rounded-md border border-border/70 bg-background/40 px-2 py-1.5">
      <details>
        <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
          <span className="shrink-0 rounded bg-muted px-1 font-mono text-[11px]">{r.id}</span>
          <span className="line-clamp-2 min-w-0 flex-1 text-[13px] font-medium leading-snug" title={shortTitle(r)}>{shortTitle(r)}</span>
          <Progress met={p.met} total={p.total} />
        </summary>
        <p className="m-0 mt-1.5 text-xs leading-relaxed text-muted-foreground">{r.text}</p>
        {r.checks.length > 0 && (
          <ul className="m-0 mt-1.5 flex list-none flex-col gap-1 p-0">
            {r.checks.map((c) => {
              const state: CheckState = data.checks?.[c.id]?.state ?? c.state;
              const mark = CHECK_MARK[state] ?? CHECK_MARK.unknown;
              return (
                <li key={c.id} className="flex gap-1.5 text-xs leading-snug">
                  <span className={cn("shrink-0", mark.cls)} aria-label={mark.label}>{mark.icon}</span>
                  <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{c.id}</span>
                  <span className="min-w-0">{c.text}</span>
                </li>
              );
            })}
          </ul>
        )}
      </details>
    </li>
  );
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="border-t border-border pt-2 first-of-type:border-t-0 first-of-type:pt-0">
      <h4 className="m-0 mb-1.5 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}<span className="font-mono">{count}</span>
      </h4>
      {children}
    </section>
  );
}

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
      className="flex w-full shrink-0 flex-col gap-3 rounded-lg border border-border bg-card p-3 text-card-foreground lg:sticky lg:top-3 lg:max-h-[calc(100vh-6rem)] lg:w-80 lg:overflow-y-auto"
      data-testid="follow-panel"
    >
      <h3 className="m-0 text-sm font-semibold">What the team must follow</h3>
      {requirements.length > 0 && (
        <Section title="Requirements" count={requirements.length}>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {requirements.map((r) => <RequirementRow key={r.id} r={r} data={data} />)}
          </ul>
          <p className="m-0 mt-1 text-[11px] text-muted-foreground">Open one for its statement and checks.</p>
        </Section>
      )}
      {decisions.length > 0 && (
        <Section title="Decisions" count={decisions.length}>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {decisions.map((d) => (
              <li key={d.id} className="rounded-md border border-border/70 bg-background/40 px-2 py-1.5">
                <details>
                  <summary className="flex cursor-pointer list-none items-start gap-2 [&::-webkit-details-marker]:hidden">
                    <span className="shrink-0 rounded bg-muted px-1 font-mono text-[11px]">{d.id}</span>
                    <span className="min-w-0 flex-1 text-[13px] font-medium leading-snug">{d.title}</span>
                  </summary>
                  <p className="m-0 mt-1.5 text-xs leading-relaxed text-muted-foreground">{d.choice}</p>
                </details>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {rules.length > 0 && (
        <Section title="Rules" count={rules.length}>
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
            {rules.map((r) => {
              const unmet = r.verdicts?.unmet ?? 0;
              return (
                <li key={r.id} className="flex gap-1.5 text-xs leading-snug">
                  <span
                    className={cn("shrink-0 font-semibold", unmet > 0 ? "text-status-failed" : "text-status-complete")}
                    aria-label={unmet > 0 ? "rule flagged" : "rule met"}
                  >
                    {unmet > 0 ? "!" : "✓"}
                  </span>
                  <span className="min-w-0">
                    <span className="line-clamp-3" title={r.text}>{r.text}</span>
                    {(r.strength === "binding" || unmet > 0) && (
                      <span className="mt-0.5 flex gap-1.5 text-[11px]">
                        {r.strength === "binding" && <span className="text-muted-foreground">binding</span>}
                        {unmet > 0 && <span className="text-status-failed">{unmet} unmet</span>}
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </Section>
      )}
      <p className="m-0 text-[11px] text-muted-foreground">
        A flagged rule goes back to the builder as a review issue. If it repeats, the overseer steps in.
      </p>
    </aside>
  );
}
