import type { TaskColumn, ThreadKind, TypedSignal } from "../../lib/runEvents.gen";
import type { CardView } from "../../stores/runEvents";

// Pure helpers for the task board: column layout, grouping, reprioritising and the
// acceptance checklist. No React and no I/O, so they are cheap to test.

export interface ColumnSpec {
  id: TaskColumn;
  label: string;
  /** Static Tailwind class (a literal, so the JIT sees it) painting the column's `--col-*` token. */
  accent: string;
}

/** Board columns in display order. The ids match `TaskColumn` and the `--col-*` tokens. */
export const COLUMNS: readonly ColumnSpec[] = [
  { id: "todo", label: "To do", accent: "border-t-col-todo" },
  { id: "in_progress", label: "In progress", accent: "border-t-col-in_progress" },
  { id: "review", label: "Review", accent: "border-t-col-review" },
  { id: "done", label: "Done", accent: "border-t-col-done" },
  { id: "blocked", label: "Blocked", accent: "border-t-col-blocked" },
  { id: "needs_attention", label: "Needs attention", accent: "border-t-col-needs_attention" },
];

export const COLUMN_LABEL: Record<TaskColumn, string> = Object.fromEntries(
  COLUMNS.map((c) => [c.id, c.label]),
) as Record<TaskColumn, string>;

/**
 * Review column WIP cap. Mirrors `BoardLimits.review_column_cap` in core/harness/task_board.py:
 * authors start no new card while Review holds this many.
 * TODO: expose the run's real cap through the cards API instead of mirroring the default.
 */
export const DEFAULT_REVIEW_CAP = 3;

/** Card aging limits in ms per size, mirroring `AGE_LIMITS` in the backend (S 10 min, M 25 min). */
export const AGE_LIMIT_MS: Record<string, number> = { S: 600_000, M: 1_500_000, L: 3_600_000 };

/** Columns where a stuck card is worth flagging (waiting in todo/done/blocked is normal). */
const AGING_COLUMNS: ReadonlySet<TaskColumn> = new Set(["in_progress", "review"]);

export const THREAD_KIND_LABEL: Record<ThreadKind, string> = {
  note: "Note",
  blocked_on: "Blocked on",
  review_issue: "Review issue",
  decision: "Decision",
  intervention: "Overseer",
  human_comment: "Human",
  artifact: "Artifact",
  handoff: "Handoff",
};

/** Text glyphs (not emoji) standing in for icons; each is aria-hidden next to a text label. */
export const THREAD_KIND_ICON: Record<ThreadKind, string> = {
  note: "✎",
  blocked_on: "⊘",
  review_issue: "⚑",
  decision: "◆",
  intervention: "⚙",
  human_comment: "☺",
  artifact: "▣",
  handoff: "⇢",
};

function naturalId(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true });
}

/** Display order inside one lane cell: highest priority first, ties by id. */
export function byPriority(priority: (c: CardView) => number) {
  return (a: CardView, b: CardView): number => priority(b) - priority(a) || naturalId(a.id, b.id);
}

export interface Lane {
  role: string;
  cells: Record<TaskColumn, CardView[]>;
}

function emptyCells(): Record<TaskColumn, CardView[]> {
  return { todo: [], in_progress: [], review: [], done: [], blocked: [], needs_attention: [] };
}

/**
 * Group cards into role swimlanes. Lane order follows `roleOrder` (the team's order),
 * then any other owner alphabetically. Cards in a cell are sorted by priority.
 * Unknown columns (a future backend value) fall back to todo rather than vanishing.
 */
export function buildLanes(
  cards: readonly CardView[],
  roleOrder: readonly string[],
  priority: (c: CardView) => number = (c) => c.priority,
): Lane[] {
  const lanes = new Map<string, Lane>();
  for (const card of cards) {
    const role = card.owner || "unassigned";
    let lane = lanes.get(role);
    if (!lane) {
      lane = { role, cells: emptyCells() };
      lanes.set(role, lane);
    }
    (lane.cells[card.column] ?? lane.cells.todo).push(card);
  }
  const cmp = byPriority(priority);
  for (const lane of lanes.values()) {
    for (const col of COLUMNS) lane.cells[col.id].sort(cmp);
  }
  const known = roleOrder.filter((r) => lanes.has(r));
  const rest = [...lanes.keys()].filter((r) => !roleOrder.includes(r)).sort();
  return [...known, ...rest].map((r) => lanes.get(r) as Lane);
}

/** Column totals across all lanes. */
export function columnCounts(cards: readonly CardView[]): Record<TaskColumn, number> {
  const counts: Record<TaskColumn, number> = {
    todo: 0, in_progress: 0, review: 0, done: 0, blocked: 0, needs_attention: 0,
  };
  for (const c of cards) counts[c.column] = (counts[c.column] ?? 0) + 1;
  return counts;
}

export interface PriorityItem {
  id: string;
  priority: number;
}

/**
 * Priorities to PATCH after moving card `id` to `toIndex` within one cell.
 *
 * `ordered` is the cell in its current display order (highest priority first, so the top of
 * the list runs first). Returns `{ cardId: newPriority }` for the cards whose priority must
 * change, or `{}` for a no-op. Only the moved card changes when an integer fits between its
 * new neighbours (or beyond the ends); otherwise the cell is renumbered in descending order.
 */
export function computeReorder(
  ordered: readonly PriorityItem[],
  id: string,
  toIndex: number,
): Record<string, number> {
  const from = ordered.findIndex((c) => c.id === id);
  if (from < 0 || ordered.length < 2) return {};
  const to = Math.max(0, Math.min(ordered.length - 1, toIndex));
  if (to === from) return {};
  const next = ordered.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved as PriorityItem);
  const above = next[to - 1];
  const below = next[to + 1];
  const self = moved as PriorityItem;

  let p: number | null = null;
  if (!above && below) p = below.priority + 1;
  else if (above && !below) p = above.priority - 1;
  else if (above && below) {
    const gap = above.priority - below.priority;
    if (gap >= 2) p = below.priority + Math.floor(gap / 2);
  }
  if (p !== null) return p === self.priority ? {} : { [id]: p };

  // No integer fits between equal or adjacent neighbours: renumber the whole cell.
  const base = Math.max(...ordered.map((c) => c.priority));
  const out: Record<string, number> = {};
  next.forEach((c, k) => {
    if (c.priority !== base - k) out[c.id] = base - k;
  });
  return out;
}

/** "5m", "2h 5m", "3d": how long a card has been in its column. Empty when unknown. */
export function formatAge(sinceIso: string | undefined, now: number): string {
  if (!sinceIso) return "";
  const t = Date.parse(sinceIso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  if (s < 60) return "<1m";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d`;
}

/** True when a card has sat in an active column longer than its size allows. */
export function isAged(card: CardView, now: number): boolean {
  if (!card.since || !AGING_COLUMNS.has(card.column)) return false;
  const limit = AGE_LIMIT_MS[card.size];
  const t = Date.parse(card.since);
  return limit !== undefined && !Number.isNaN(t) && now - t > limit;
}

export interface BlockedOn {
  role: string | null;
  /** The unblock card id when the backend recorded one, else the first unfinished dependency. */
  card: string | null;
}

/** What a blocked card waits on: the latest `blocked_on` thread entry, else its open dependencies. */
export function blockedOn(card: CardView, cards: Record<string, CardView>): BlockedOn | null {
  if (card.column !== "blocked") return null;
  for (let i = card.thread.length - 1; i >= 0; i--) {
    const e = card.thread[i];
    if (e?.kind !== "blocked_on") continue;
    const data = e.data ?? {};
    const role = typeof data["role"] === "string" ? data["role"] : null;
    const unblock = typeof data["unblock_card"] === "string" ? data["unblock_card"] : null;
    return { role, card: unblock };
  }
  const open = card.dependsOn.find((d) => cards[d] && cards[d]?.column !== "done");
  return { role: open ? (cards[open]?.owner ?? null) : null, card: open ?? card.dependsOn[0] ?? null };
}

export interface AcceptanceItem {
  text: string;
  met: boolean;
  evidence?: string;
}

interface RawCheck {
  item: string;
  met: boolean;
  note?: string;
}

function checksFrom(result: Record<string, unknown>): RawCheck[] {
  const out: RawCheck[] = [];
  for (const key of ["acceptance", "items"] as const) {
    const list = result[key];
    if (!Array.isArray(list)) continue;
    for (const raw of list) {
      if (typeof raw !== "object" || raw === null) continue;
      const r = raw as Record<string, unknown>;
      if (typeof r["item"] !== "string" || typeof r["met"] !== "boolean") continue;
      const note =
        typeof r["evidence"] === "string" ? r["evidence"] : typeof r["detail"] === "string" ? r["detail"] : undefined;
      out.push({ item: r["item"].trim(), met: r["met"], note });
    }
  }
  return out;
}

/**
 * The card's acceptance checklist with met state.
 *
 * Met state is read from the latest verdict per item: the owner's AGENT_RESULT `acceptance`
 * and the reviewer's `items` for this task (in arrival order, later wins), plus the thread's
 * "✓/✗ item" notes and `review_issue` entries as a fallback when the run was loaded from disk
 * without signals. A done card is fully met (the reviewer passed every item). Items nobody has
 * judged yet are unchecked.
 */
export function acceptanceState(card: CardView, signals: readonly TypedSignal[]): AcceptanceItem[] {
  const state = new Map<string, { met: boolean; evidence?: string }>();
  for (const entry of card.thread) {
    if (entry.kind === "note") {
      const m = /^([✓✗])\s+(.+?)(?:\s+—\s+(.*))?$/su.exec(entry.text);
      if (m) state.set((m[2] as string).trim(), { met: m[1] === "✓", evidence: m[3] });
    } else if (entry.kind === "review_issue") {
      const item = entry.data?.["item"];
      if (typeof item === "string") state.set(item.trim(), { met: false, evidence: entry.text });
    }
  }
  for (const sig of signals) {
    if (sig.type !== "AGENT_RESULT" || !sig.data) continue;
    const result = sig.data.result;
    if (!result || result["task_id"] !== card.id) continue;
    for (const c of checksFrom(result)) state.set(c.item, { met: c.met, evidence: c.note });
  }
  const done = card.column === "done";
  return card.acceptance.map((text) => {
    const s = state.get(text.trim());
    return { text, met: done || (s?.met ?? false), evidence: s?.evidence };
  });
}

/** Merge ranges `[base, head]` recorded on the card's thread (`data.merge`), oldest first. */
export function mergeRanges(card: CardView): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const e of card.thread) {
    const merge = e.data?.["merge"];
    if (Array.isArray(merge) && merge.length === 2 && merge.every((s) => typeof s === "string")) {
      out.push([merge[0] as string, merge[1] as string]);
    }
  }
  return out;
}
