import { useEffect, useRef, useState } from "react";
import { runsApi } from "../api";
import type { ContextSnapshot, IterationMetrics } from "../api";

interface Props {
  runId: string;
  isActive: boolean;
}

// ── Stacked bar for context composition ────────────────────────────────────

const SEGMENT_COLORS: Record<string, string> = {
  repo_map: "#6366f1",
  git_diff: "#f59e0b",
  files: "#10b981",
  other: "#94a3b8",
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
    <div style={{ display: "flex", height: 14, borderRadius: 3, overflow: "hidden", width: "100%", background: "#f1f5f9" }}>
      {segments.map((seg) => (
        <div
          key={seg.label}
          title={`${seg.label}: ${seg.tokens} tokens`}
          style={{
            width: `${(seg.tokens / total) * 100}%`,
            background: seg.color,
            minWidth: seg.tokens > 0 ? 2 : 0,
          }}
        />
      ))}
    </div>
  );
}

// ── Legend ─────────────────────────────────────────────────────────────────

function BarLegend() {
  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
      {Object.entries(SEGMENT_COLORS).map(([label, color]) => (
        <span key={label} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "#64748b" }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: color, display: "inline-block" }} />
          {label}
        </span>
      ))}
    </div>
  );
}

// ── Totals by role ─────────────────────────────────────────────────────────

function RoleTotals({ rows }: { rows: IterationMetrics[] }) {
  const byRole: Record<string, { input: number; output: number; cache: number; iters: number }> = {};
  for (const r of rows) {
    if (!byRole[r.role]) byRole[r.role] = { input: 0, output: 0, cache: 0, iters: 0 };
    byRole[r.role].input += r.input_tokens;
    byRole[r.role].output += r.output_tokens;
    byRole[r.role].cache += r.cache_read_tokens;
    byRole[r.role].iters += 1;
  }
  const roles = Object.keys(byRole).sort();

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
      <thead>
        <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
          <th style={TH_LEFT}>role</th>
          <th style={TH}>iters</th>
          <th style={TH}>input</th>
          <th style={TH}>output</th>
          <th style={TH}>cache_read</th>
        </tr>
      </thead>
      <tbody>
        {roles.map((role) => {
          const t = byRole[role];
          return (
            <tr key={role} style={{ borderBottom: "1px solid #f1f5f9" }}>
              <td style={TD_LEFT}><code style={{ fontSize: 11 }}>{role}</code></td>
              <td style={TD}>{t.iters}</td>
              <td style={TD}>{t.input.toLocaleString()}</td>
              <td style={TD}>{t.output.toLocaleString()}</td>
              <td style={TD}>{t.cache.toLocaleString()}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────────

const TH: React.CSSProperties = {
  padding: "6px 12px",
  textAlign: "center",
  fontWeight: 600,
  fontSize: 11,
  color: "#64748b",
  textTransform: "uppercase",
  letterSpacing: "0.03em",
  whiteSpace: "nowrap",
};

const TH_LEFT: React.CSSProperties = { ...TH, textAlign: "left" };

const TD: React.CSSProperties = {
  padding: "5px 12px",
  fontSize: 12,
  color: "#334155",
  textAlign: "center",
};

const TD_LEFT: React.CSSProperties = { ...TD, textAlign: "left" };

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
    return <div style={{ padding: 16, color: "#ef4444", fontSize: 13 }}>{error}</div>;
  }

  const sorted = [...metrics].reverse();

  return (
    <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 24 }}>
      {/* ── Per-iteration table ────────────────────────────────────────── */}
      <section>
        <div style={{ fontWeight: 600, fontSize: 12, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
          Iterations ({metrics.length})
        </div>
        {metrics.length === 0 ? (
          <div style={{ color: "#94a3b8", fontSize: 13 }}>No data yet.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                  <th style={TH_LEFT}>role</th>
                  <th style={TH}>iter</th>
                  <th style={TH}>model</th>
                  <th style={TH}>input</th>
                  <th style={TH}>output</th>
                  <th style={TH}>cache_read</th>
                  <th style={TH}>elapsed (s)</th>
                  <th style={TH_LEFT}>tools</th>
                  <th style={TH}>no_op</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid #f1f5f9", background: r.failed ? "#fff1f2" : undefined }}>
                    <td style={TD_LEFT}><code style={{ fontSize: 11 }}>{r.role}</code></td>
                    <td style={TD}>{r.iteration}</td>
                    <td style={{ ...TD, fontSize: 11, color: "#64748b" }}>{r.model.split("-").slice(-2).join("-")}</td>
                    <td style={TD}>{r.input_tokens.toLocaleString()}</td>
                    <td style={TD}>{r.output_tokens.toLocaleString()}</td>
                    <td style={TD}>{r.cache_read_tokens.toLocaleString()}</td>
                    <td style={TD}>{r.elapsed_seconds.toFixed(1)}</td>
                    <td style={{ ...TD_LEFT, fontSize: 11, color: "#64748b" }}>{r.tools_called.slice(-4).join(", ")}</td>
                    <td style={TD}>{r.no_op ? "✓" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Token totals by role ───────────────────────────────────────── */}
      {metrics.length > 0 && (
        <section>
          <div style={{ fontWeight: 600, fontSize: 12, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            Totals by Role
          </div>
          <RoleTotals rows={metrics} />
        </section>
      )}

      {/* ── Context composition ────────────────────────────────────────── */}
      {ctxLog.length > 0 && (
        <section>
          <div style={{ fontWeight: 600, fontSize: 12, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            Context Composition ({ctxLog.length} calls)
          </div>
          <BarLegend />
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {ctxLog.map((snap, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ fontSize: 11, color: "#64748b", width: 120, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {snap.role} #{snap.iteration}
                </div>
                <div style={{ flex: 1 }}>
                  <ContextBar snap={snap} />
                </div>
                <div style={{ fontSize: 11, color: "#94a3b8", width: 60, textAlign: "right", flexShrink: 0 }}>
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
