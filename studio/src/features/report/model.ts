import type { CostRow } from "./api";

// Pure helpers for the run report.

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function formatUsd(n: number): string {
  return n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(n === 0 ? 2 : 4)}`;
}

/** "12m 03s", "1h 05m", or null when either end is missing or unparsable. */
export function formatSpan(startIso: string | null | undefined, endIso: string | null | undefined): string | null {
  if (!startIso || !endIso) return null;
  const ms = Date.parse(endIso) - Date.parse(startIso);
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = Math.round(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}

export interface CostBar extends CostRow {
  tokens: number;
  /** Share of the largest role's tokens, 0 to 100, for the bar length. */
  pct: number;
}

/** Rows with token totals and a bar length relative to the biggest spender (tokens only). */
export function costBars(rows: readonly CostRow[]): CostBar[] {
  const withTokens = rows.map((r) => ({ ...r, tokens: r.input_tokens + r.output_tokens }));
  const max = Math.max(1, ...withTokens.map((r) => r.tokens));
  return withTokens.map((r) => ({ ...r, pct: Math.round((r.tokens / max) * 100) }));
}
