import type { PlaybookEntryWire, RunMetricsPoint } from "../report/api";

// Pure model for the project insights: sparkline geometry and text, series and playbook grouping.

export interface SparkPoint {
  x: number;
  y: number;
}

/** Points for `values` in a `width` x `height` box with `pad` around it; one point per value. */
export function sparklinePoints(values: readonly number[], width = 120, height = 32, pad = 3): SparkPoint[] {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const innerW = width - 2 * pad;
  const innerH = height - 2 * pad;
  return values.map((v, i) => ({
    x: values.length === 1 ? width / 2 : pad + (i / (values.length - 1)) * innerW,
    // A flat series sits mid-height; larger values are higher on screen.
    y: span === 0 ? height / 2 : pad + (1 - (v - min) / span) * innerH,
  }));
}

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(Math.abs(n) < 1 ? 3 : 1);
}

/** The accessible text for a series: label, count, latest, change, range. */
export function sparklineSummary(label: string, values: readonly number[]): string {
  if (values.length === 0) return `${label}: no runs recorded yet.`;
  const latest = values[values.length - 1] as number;
  const noun = values.length === 1 ? "run" : "runs";
  const head = `${label}: ${values.length} ${noun}, latest ${fmt(latest)}`;
  if (values.length === 1) return `${head}.`;
  const prev = values[values.length - 2] as number;
  const dir = latest > prev ? "up from" : latest < prev ? "down from" : "same as";
  return `${head}, ${dir} ${fmt(prev)}, range ${fmt(Math.min(...values))} to ${fmt(Math.max(...values))}.`;
}

export interface SeriesSpec {
  key: string;
  label: string;
  /** Which direction is better, for the caption only. */
  better: "lower" | "higher";
}

export const SERIES: readonly SeriesSpec[] = [
  { key: "stall_minutes", label: "Stall minutes", better: "lower" },
  { key: "interventions", label: "Overseer interventions", better: "lower" },
  { key: "interventions_worked", label: "Interventions that worked", better: "higher" },
  { key: "review_rounds_used", label: "Review rounds used", better: "lower" },
  { key: "cards_done", label: "Cards done", better: "higher" },
  { key: "cost_usd", label: "Cost (USD)", better: "lower" },
  { key: "cost_per_completed_role", label: "Cost per completed role (USD)", better: "lower" },
];

/** Values of one metric across runs, skipping runs that did not record it. */
export function seriesValues(runs: readonly RunMetricsPoint[], key: string): { runId: string; value: number }[] {
  const out: { runId: string; value: number }[] = [];
  for (const r of runs) {
    const v = r.metrics[key];
    if (typeof v === "number" && Number.isFinite(v)) out.push({ runId: r.run_id, value: v });
  }
  return out;
}

export const SECTION_ORDER = [
  "team_adjustments", "brief_addenda", "known_pitfalls", "review_rounds", "capability_hints", "task_templates",
] as const;

export const SECTION_LABEL: Record<string, string> = {
  team_adjustments: "Team adjustments",
  brief_addenda: "Brief addenda",
  known_pitfalls: "Known pitfalls",
  review_rounds: "Review rounds",
  capability_hints: "Capability hints",
  task_templates: "Task templates",
};

const STATE_RANK: Record<string, number> = { active: 0, emerging: 1, declining: 2, superseded: 3, archived: 4 };

export interface PlaybookGroup {
  section: string;
  label: string;
  entries: PlaybookEntryWire[];
}

/** Entries grouped by section in a fixed order; within a section live states first, then by confidence. */
export function groupPlaybook(entries: readonly PlaybookEntryWire[]): PlaybookGroup[] {
  const known = new Set<string>(SECTION_ORDER);
  const order = [...SECTION_ORDER, ...[...new Set(entries.map((e) => e.section))].filter((s) => !known.has(s)).sort()];
  const groups: PlaybookGroup[] = [];
  for (const section of order) {
    const list = entries
      .filter((e) => e.section === section)
      .sort((a, b) => (STATE_RANK[a.state] ?? 5) - (STATE_RANK[b.state] ?? 5) || b.confidence - a.confidence);
    if (list.length > 0) groups.push({ section, label: SECTION_LABEL[section] ?? section, entries: list });
  }
  return groups;
}

/** A playbook value as readable text: strings as they are, everything else as compact JSON. */
export function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && value.every((v) => typeof v === "string")) return value.join("; ");
  return JSON.stringify(value);
}
