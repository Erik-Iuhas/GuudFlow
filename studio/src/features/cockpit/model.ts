import type { OverseerInterventionDetail } from "../../lib/runEvents.gen";
import type { AgentState, AgentView, BlockedOnView, ReviewRoundView } from "../../stores/runEvents";
import type { AgentConfig } from "../../types";

// Pure helpers for the cockpit: the pipeline graph model, the budget math and small
// formatters. Kept free of React so they are cheap to test.

/** The overseer is a detached role (plan section 11): no depends_on edges, shown apart. */
export const OVERSEER_ROLE = "overseer";

export interface RoleSpec {
  role: string;
  dependsOn: string[];
  /** Roles whose work this role reviews (review edges run reviewer -> author). */
  reviewOf: string[];
  maxRounds: number | null;
}

/** Roles and edges from the project's agent list. */
export function roleSpecs(agents: readonly AgentConfig[] | undefined): RoleSpec[] {
  return (agents ?? []).map((a) => ({
    role: a.role,
    dependsOn: a.depends_on ?? [],
    reviewOf: a.review_of ?? [],
    maxRounds: typeof a.max_review_rounds === "number" ? a.max_review_rounds : null,
  }));
}

/**
 * Specs for every role the cockpit should show: the project's roles first, then any role
 * that has only appeared on the board (so a run with no project config still draws).
 */
export function mergeRoles(specs: readonly RoleSpec[], seen: Iterable<string>): RoleSpec[] {
  const out = [...specs];
  const known = new Set(out.map((s) => s.role));
  for (const role of seen) {
    if (role === "runner" || known.has(role)) continue;
    known.add(role);
    out.push({ role, dependsOn: [], reviewOf: [], maxRounds: null });
  }
  return out;
}

// ── Graph ────────────────────────────────────────────────────────────────────

export type EdgeKind = "depends_on" | "review" | "blocked";

export interface GraphNodeModel {
  id: string;
  role: string;
  state: AgentState;
  detached: boolean;
  /** Pulsing: the overseer while it is running. */
  pulsing: boolean;
  /** Layer (column) and row for the layout. */
  x: number;
  y: number;
  card?: string;
}

export interface GraphEdgeModel {
  id: string;
  kind: EdgeKind;
  source: string;
  target: string;
  label?: string;
}

export interface GraphModel {
  nodes: GraphNodeModel[];
  edges: GraphEdgeModel[];
}

export const NODE_WIDTH = 168;
export const NODE_HEIGHT = 56;
const COL_GAP = 56;
const ROW_GAP = 24;

/** Longest dependency chain ending at each role (cycles are cut, not followed). */
function layers(specs: readonly RoleSpec[]): Map<string, number> {
  const byRole = new Map(specs.map((s) => [s.role, s]));
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  function visit(role: string): number {
    const known = depth.get(role);
    if (known !== undefined) return known;
    if (visiting.has(role)) return 0;
    visiting.add(role);
    let d = 0;
    for (const dep of byRole.get(role)?.dependsOn ?? []) {
      if (byRole.has(dep)) d = Math.max(d, visit(dep) + 1);
    }
    visiting.delete(role);
    depth.set(role, d);
    return d;
  }
  for (const s of specs) visit(s.role);
  return depth;
}

/** Edge label for a review edge: `round 2/3`, or `round 2` without a cap, `no rework` at zero. */
export function reviewLabel(round: number, max: number | null): string {
  if (round <= 0) return max ? `round 0/${max}` : "round 0";
  return max ? `round ${round}/${max}` : `round ${round}`;
}

/**
 * The graph for the current store state: layered left to right by depends_on, review edges
 * (reviewer -> author) labelled with the rework round, dashed blocked-on arrows, and the
 * overseer parked below the pipeline with no edges.
 */
/** "LR": layers left to right (wide containers); "TB": top to bottom (tall side panel). */
export type GraphDirection = "LR" | "TB";

export function buildGraph(
  specs: readonly RoleSpec[],
  agents: Readonly<Record<string, AgentView>>,
  reviews: Readonly<Record<string, ReviewRoundView>>,
  blockedOn: Readonly<Record<string, BlockedOnView>>,
  direction: GraphDirection = "LR",
): GraphModel {
  const place = (layer: number, row: number) => direction === "LR"
    ? { x: layer * (NODE_WIDTH + COL_GAP), y: row * (NODE_HEIGHT + ROW_GAP) }
    : { x: row * (NODE_WIDTH + ROW_GAP), y: layer * (NODE_HEIGHT + COL_GAP) };
  const pipeline = specs.filter((s) => s.role !== OVERSEER_ROLE);
  const overseer = specs.find((s) => s.role === OVERSEER_ROLE);
  const depth = layers(pipeline);
  const rows = new Map<number, number>();
  const nodes: GraphNodeModel[] = [];
  let maxRow = 0;
  let maxLayer = 0;

  for (const s of pipeline) {
    const layer = depth.get(s.role) ?? 0;
    const row = rows.get(layer) ?? 0;
    rows.set(layer, row + 1);
    maxRow = Math.max(maxRow, row);
    maxLayer = Math.max(maxLayer, layer);
    const a = agents[s.role];
    nodes.push({
      id: s.role, role: s.role, state: a?.state ?? "idle", detached: false, pulsing: false,
      ...place(layer, row), card: a?.card,
    });
  }
  if (overseer || agents[OVERSEER_ROLE]) {
    const a = agents[OVERSEER_ROLE];
    const state = a?.state ?? "idle";
    nodes.push({
      id: OVERSEER_ROLE, role: OVERSEER_ROLE, state, detached: true, pulsing: state === "running",
      // Parked away from the pipeline: below it (LR) or after its last layer (TB).
      ...(direction === "LR"
        ? { x: 0, y: (maxRow + 1) * (NODE_HEIGHT + ROW_GAP) + ROW_GAP }
        : { x: 0, y: (maxLayer + 1) * (NODE_HEIGHT + COL_GAP) + ROW_GAP }),
      card: a?.card,
    });
  }

  const ids = new Set(nodes.map((n) => n.id));
  const edges: GraphEdgeModel[] = [];
  for (const s of pipeline) {
    for (const dep of s.dependsOn) {
      if (ids.has(dep)) edges.push({ id: `dep:${dep}>${s.role}`, kind: "depends_on", source: dep, target: s.role });
    }
  }
  for (const s of pipeline) {
    for (const author of s.reviewOf) {
      if (!ids.has(author)) continue;
      const round = reviews[`${s.role}>${author}`]?.round ?? 0;
      edges.push({
        id: `review:${s.role}>${author}`, kind: "review", source: s.role, target: author,
        label: reviewLabel(round, s.maxRounds),
      });
    }
  }
  // A review edge that exists only because a REJECTED signal arrived (no project config).
  for (const r of Object.values(reviews)) {
    const id = `review:${r.reviewer}>${r.author}`;
    if (edges.some((e) => e.id === id) || !ids.has(r.reviewer) || !ids.has(r.author)) continue;
    edges.push({ id, kind: "review", source: r.reviewer, target: r.author, label: reviewLabel(r.round, null) });
  }
  for (const b of Object.values(blockedOn)) {
    if (!ids.has(b.role) || !ids.has(b.on)) continue;
    edges.push({
      id: `blocked:${b.role}>${b.on}`, kind: "blocked", source: b.role, target: b.on, label: "blocked on",
    });
  }
  return { nodes, edges };
}

/** Text shown next to a state's colour (colour alone never carries the state). */
export const STATE_LABEL: Record<AgentState, string> = {
  idle: "idle",
  running: "running",
  complete: "complete",
  failed: "failed",
  blocked: "blocked",
  waiting: "waiting",
};

// ── Budget ───────────────────────────────────────────────────────────────────

export type BudgetLevel = "none" | "ok" | "warn" | "over";

export interface BudgetSummary {
  spent: number;
  cap: number | null;
  /** Fraction of the cap spent (can exceed 1); null without a cap. */
  fraction: number | null;
  remaining: number | null;
  level: BudgetLevel;
  /** Per-role spend, largest first, zero rows dropped. */
  perAgent: { role: string; cost: number; share: number }[];
}

/** Warn at 80% of the cap, "over" at 100%. */
export const BUDGET_WARN_FRACTION = 0.8;

export function computeBudget(costs: Readonly<Record<string, number>>, cap: number | null | undefined): BudgetSummary {
  const rows = Object.entries(costs).filter(([, c]) => Number.isFinite(c) && c > 0);
  const spent = rows.reduce((sum, [, c]) => sum + c, 0);
  const hasCap = typeof cap === "number" && Number.isFinite(cap) && cap > 0;
  const fraction = hasCap ? spent / cap : null;
  let level: BudgetLevel = "none";
  if (fraction !== null) level = fraction >= 1 ? "over" : fraction >= BUDGET_WARN_FRACTION ? "warn" : "ok";
  return {
    spent,
    cap: hasCap ? cap : null,
    fraction,
    remaining: hasCap ? Math.max(0, cap - spent) : null,
    level,
    perAgent: rows
      .map(([role, cost]) => ({ role, cost, share: spent > 0 ? cost / spent : 0 }))
      .sort((a, b) => b.cost - a.cost || a.role.localeCompare(b.role)),
  };
}

export function formatUsd(n: number): string {
  return `$${n.toFixed(n > 0 && n < 0.1 ? 4 : 2)}`;
}

// ── Misc formatters ──────────────────────────────────────────────────────────

/** Parse a signal timestamp; a naive ISO string is UTC, as the backend writes it. */
export function parseTimestamp(ts: string | undefined): number | null {
  if (!ts) return null;
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(ts);
  const ms = Date.parse(hasZone ? ts : `${ts}Z`);
  return Number.isFinite(ms) ? ms : null;
}

/** `75` -> `1:15`, `3725` -> `1:02:05`. */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(sec).padStart(2, "0")}`;
}

/** What an intervention acts on: a role, a card, a reassignment, or a review edge. */
export function interventionTarget(d: OverseerInterventionDetail): string {
  const parts: string[] = [];
  if (d.from_role && d.to_role) parts.push(`${d.from_role} -> ${d.to_role}`);
  else if (d.role) parts.push(d.role);
  if (d.author_role && d.reviewer_role) parts.push(`${d.reviewer_role} reviews ${d.author_role}`);
  if (d.task_id) parts.push(`card ${d.task_id}`);
  return parts.join(" · ") || "run";
}
