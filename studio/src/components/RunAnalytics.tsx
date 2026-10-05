import { useEffect, useRef, useState } from "react";
import { runsApi } from "../api";
import type { ContextSnapshot, IterationMetrics } from "../api";
import { cn } from "../lib/utils";

interface Props {
  runId: string;
  isActive: boolean;
}

// ── Stacked bar for context composition ────────────────────────────────────

const SEGMENT_COLORS: Record<string, string> = {
  repo_map: "bg-status-running",
  git_diff: "bg-status-blocked",
  files: "bg-status-complete",
  other: "bg-muted-foreground",
};

function ContextBar({ snap }: { snap: ContextSnapshot }) {
  const total = snap.used_tokens || 1;
  const filesTokens = snap.relevant_files.reduce((s, [, t]) => s + t, 0);
  const other = Math.max(
    0,
    total - snap.repo_map_tokens - snap.git_diff_tokens - filesTokens,
  );

  const segments: { label: string; tokens: number; color: string }[] = [
    { label: "repo_map", tokens: snap.repo_map_tokens, color: SEGMENT_COLORS.repo_map },
    { label: "git_diff", tokens: snap.git_diff_tokens, color: SEGMENT_COLORS.git_diff },
    { label: "files", tokens: filesTokens, color: SEGMENT_COLORS.files },
    { label: "other", tokens: other, color: SEGMENT_COLORS.other },
  ].filter((s) => s.tokens > 0);

  return (
    <div className="flex h-3.5 w-full overflow-hidden rounded-sm bg-muted">
      {segments.map((seg) => (
        <div
          key={seg.label}
          title={`${seg.label}: ${seg.tokens} tokens`}
          className={cn("min-w-[2px] w-[var(--seg)]", seg.color)}
          // The width is a data-driven percentage, so it travels as a CSS variable.
          style={{ "--seg": `${(seg.tokens / total) * 100}%` } as React.CSSProperties}
        />
      ))}
    </div>
  );
}

// ── Legend ─────────────────────────────────────────────────────────────────

function BarLegend() {
  return (
    <div className="mb-2 flex flex-wrap gap-3">
      {Object.entries(SEGMENT_COLORS).map(([label, color]) => (
        <span key={label} className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <span className={cn("inline-block h-2.5 w-2.5 rounded-sm", color)} />
          {label}
        </span>
      ))}
    </div>
  );
}

// ── Totals by role ─────────────────────────────────────────────────────────

// Track 3d.5 — format cost compactly. Sub-cent shows as "<$0.01".
function fmtCost(usd: number): string {
  if (!usd || usd <= 0) return "—";
  if (usd < 0.01) return "<$0.01";
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

const TABLE_CLS = "w-full border-collapse text-xs";
const HEAD_ROW = "border-b border-border bg-muted";
const TH = "whitespace-nowrap px-3 py-1.5 text-center text-[11px] font-semibold uppercase tracking-wide text-muted-foreground";
const TH_LEFT = cn(TH, "text-left");
const TD = "px-3 py-[5px] text-center text-xs text-foreground";
const TD_LEFT = cn(TD, "text-left");

function RoleTotals({ rows }: { rows: IterationMetrics[] }) {
  const byRole: Record<string, { input: number; output: number; cache: number; iters: number; cost: number }> = {};
  for (const r of rows) {
    if (!byRole[r.role]) byRole[r.role] = { input: 0, output: 0, cache: 0, iters: 0, cost: 0 };
    byRole[r.role].input += r.input_tokens;
    byRole[r.role].output += r.output_tokens;
    byRole[r.role].cache += r.cache_read_tokens;
    byRole[r.role].iters += 1;
    byRole[r.role].cost += r.estimated_cost_usd ?? 0;
  }
  const roles = Object.keys(byRole).sort();
  const grandTotal = roles.reduce((s, r) => s + byRole[r].cost, 0);

  return (
    <table className={TABLE_CLS}>
      <thead>
        <tr className={HEAD_ROW}>
          <th className={TH_LEFT}>role</th>
          <th className={TH}>iters</th>
          <th className={TH}>input</th>
          <th className={TH}>output</th>
          <th className={TH}>cache_read</th>
          <th className={TH} title="Estimated USD cost — Track 3d.5">cost</th>
        </tr>
      </thead>
      <tbody>
        {roles.map((role) => {
          const t = byRole[role];
          return (
            <tr key={role} className="border-b border-border">
              <td className={TD_LEFT}><code className="text-[11px]">{role}</code></td>
              <td className={TD}>{t.iters}</td>
              <td className={TD}>{t.input.toLocaleString()}</td>
              <td className={TD}>{t.output.toLocaleString()}</td>
              <td className={TD}>{t.cache.toLocaleString()}</td>
              <td className={TD}>{fmtCost(t.cost)}</td>
            </tr>
          );
        })}
        {grandTotal > 0 && (
          <tr className="border-t-2 border-border bg-muted">
            <td className={cn(TD_LEFT, "font-semibold")}>total</td>
            <td className={TD}>—</td>
            <td className={TD}>—</td>
            <td className={TD}>—</td>
            <td className={TD}>—</td>
            <td className={cn(TD, "font-semibold")}>{fmtCost(grandTotal)}</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function RunAnalytics({ runId, isActive }: Props) {
  const [metrics, setMetrics] = useState<IterationMetrics[]>([]);
  const [ctxLog, setCtxLog] = useState<ContextSnapshot[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchData() {
    try {
      const [m, c] = await Promise.all([
        runsApi.iterationMetrics(runId),
        runsApi.contextLog(runId),
      ]);
      setMetrics(m);
      setCtxLog(c);
      setError(null);
    } catch {
      setError("Could not load analytics.");
    }
  }

  useEffect(() => {
    void fetchData();
    if (isActive) {
      timerRef.current = setInterval(() => void fetchData(), 5000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, isActive]);

  if (error) {
    return <div className="p-4 text-[13px] text-destructive">{error}</div>;
  }

  const sorted = [...metrics].reverse();
  const SECTION_HEAD = "mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground";

  return (
    <div className="flex flex-col gap-6 px-4 py-3">
      {/* ── Per-iteration table ────────────────────────────────────────── */}
      <section>
        <div className={SECTION_HEAD}>Iterations ({metrics.length})</div>
        {metrics.length === 0 ? (
          <div className="text-[13px] text-muted-foreground">No data yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className={TABLE_CLS}>
              <thead>
                <tr className={HEAD_ROW}>
                  <th className={TH_LEFT}>role</th>
                  <th className={TH}>iter</th>
                  <th className={TH}>model</th>
                  <th className={TH}>input</th>
                  <th className={TH}>output</th>
                  <th className={TH}>cache_read</th>
                  <th className={TH}>elapsed (s)</th>
                  <th className={TH} title="Track 3d.5 — estimated USD cost">cost</th>
                  <th className={TH_LEFT}>tools</th>
                  <th className={TH}>no_op</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => {
                  // Track 12 — surface sub-LLM dispatch tooltip if present.
                  const subBd = r.sub_llm_breakdown ?? {};
                  const subUseCases = Object.keys(subBd);
                  const costTitle = subUseCases.length > 0
                    ? `Includes sub-LLM: ${subUseCases.map((uc) => `${uc} (${subBd[uc].calls})`).join(", ")}`
                    : undefined;
                  return (
                    <tr key={i} className={cn("border-b border-border", r.failed && "bg-status-failed/10")}>
                      <td className={TD_LEFT}><code className="text-[11px]">{r.role}</code></td>
                      <td className={TD}>{r.iteration}</td>
                      <td className={cn(TD, "text-[11px] text-muted-foreground")}>{r.model.split("-").slice(-2).join("-")}</td>
                      <td className={TD}>{r.input_tokens.toLocaleString()}</td>
                      <td className={TD}>{r.output_tokens.toLocaleString()}</td>
                      <td className={TD}>{r.cache_read_tokens.toLocaleString()}</td>
                      <td className={TD}>{r.elapsed_seconds.toFixed(1)}</td>
                      <td className={TD} title={costTitle}>
                        {fmtCost(r.estimated_cost_usd ?? 0)}
                        {subUseCases.length > 0 && (
                          <span className="ml-1 text-[10px] text-muted-foreground">+sub</span>
                        )}
                      </td>
                      <td className={cn(TD_LEFT, "text-[11px] text-muted-foreground")}>{r.tools_called.slice(-4).join(", ")}</td>
                      <td className={TD}>{r.no_op ? "✓" : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Token totals by role ───────────────────────────────────────── */}
      {metrics.length > 0 && (
        <section>
          <div className={SECTION_HEAD}>Totals by Role</div>
          <RoleTotals rows={metrics} />
        </section>
      )}

      {/* ── Context composition ────────────────────────────────────────── */}
      {ctxLog.length > 0 && (
        <section>
          <div className={SECTION_HEAD}>Context Composition ({ctxLog.length} calls)</div>
          <BarLegend />
          <div className="flex flex-col gap-1.5">
            {ctxLog.map((snap, i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="w-[120px] shrink-0 truncate text-[11px] text-muted-foreground">
                  {snap.role} #{snap.iteration}
                </div>
                <div className="flex-1">
                  <ContextBar snap={snap} />
                </div>
                <div className="w-[60px] shrink-0 text-right text-[11px] text-muted-foreground">
                  {snap.used_tokens.toLocaleString()}t
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
