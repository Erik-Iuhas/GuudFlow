import type { BacklogItemWire, ItemOutcome, RunBranchWire } from "./api";

export interface BacklogGroups {
  inRun: BacklogItemWire[];
  open: BacklogItemWire[];
  done: BacklogItemWire[];
  dropped: BacklogItemWire[];
}

/** Items by status; open ones by priority (highest first), then id. */
export function groupBacklog(items: readonly BacklogItemWire[]): BacklogGroups {
  const byPriority = (a: BacklogItemWire, b: BacklogItemWire) =>
    b.priority - a.priority || idNum(a.id) - idNum(b.id);
  const pick = (status: BacklogItemWire["status"]) => items.filter((i) => i.status === status).sort(byPriority);
  return { inRun: pick("in_run"), open: pick("open"), done: pick("done"), dropped: pick("dropped") };
}

function idNum(id: string): number {
  const n = Number(id.slice(1));
  return Number.isFinite(n) ? n : 0;
}

/**
 * The priority that moves `item` one place up or down among the open items
 * (one above / below its neighbour), or null when it is already at that end.
 */
export function movedPriority(open: readonly BacklogItemWire[], itemId: string, direction: "up" | "down"): number | null {
  const i = open.findIndex((x) => x.id === itemId);
  const j = direction === "up" ? i - 1 : i + 1;
  if (i < 0 || j < 0 || j >= open.length) return null;
  return direction === "up" ? open[j]!.priority + 1 : open[j]!.priority - 1;
}

/** Default run selection: the top `n` open items. */
export function topOpen(open: readonly BacklogItemWire[], n = 3): string[] {
  return open.slice(0, n).map((i) => i.id);
}

export const OUTCOME_LABEL: Record<ItemOutcome, string> = {
  done: "finished, waiting for your review",
  partial: "partly done",
  not_started: "not started",
};

/** Runs that wait for a decision, and the decided ones (newest first). */
export function splitRuns(runs: readonly RunBranchWire[]): { pending: RunBranchWire[]; decided: RunBranchWire[] } {
  return {
    pending: runs.filter((r) => r.state === "pending"),
    decided: runs.filter((r) => r.state !== "pending"),
  };
}

/** Parse the acceptance textarea: one item per non-empty line, list markers stripped. */
export function parseAcceptance(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/^\s*(?:[-*]\s*(?:\[[ x]\]\s*)?|\d+[.)]\s*)/i, "").trim())
    .filter((l) => l.length > 0);
}

/** "3 commits, 2 files" (singular aware). */
export function changeSummary(run: Pick<RunBranchWire, "commits" | "files">): string {
  const c = run.commits === 1 ? "1 commit" : `${run.commits} commits`;
  const f = run.files.length === 1 ? "1 file" : `${run.files.length} files`;
  return `${c}, ${f}`;
}

/** "B2, b5 B7" → ["B2", "B5", "B7"] (anything that is not an item id is ignored). */
export function parseItemIds(text: string): string[] {
  return [...new Set((text.toUpperCase().match(/\bB\d+\b/g) ?? []))];
}

/** One line of a backlog item's history (plan §12): a draft that changed it, or a run on it. */
export interface ItemEvent {
  at: number | null;
  /** "draft-4" or the run id. */
  where: string;
  /** What happened: "created", "edited: acceptance", "accepted", the run's outcome. */
  what: string;
  /** Why, in plain words (a draft's reason, or the run's summary). */
  why: string | null;
  quote: string | null;
  kind: "draft" | "run";
}

/** An item's history, newest first. */
export function itemEvents(history: Record<string, unknown>[]): ItemEvent[] {
  const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
  const out: ItemEvent[] = [];
  for (const h of history) {
    const why = (h.why ?? null) as { summary?: string; because?: string; quote?: string | null } | null;
    const at = typeof h.at === "number" ? h.at : null;
    if (str(h.draft)) {
      out.push({
        at, where: String(h.draft), kind: "draft",
        what: str(h.change) ?? str(h.decision) ?? "changed",
        why: why ? (why.summary ?? why.because ?? null) : null,
        quote: why?.quote ?? null,
      });
    } else if (str(h.run_id)) {
      out.push({
        at, where: String(h.run_id), kind: "run",
        what: str(h.decision) ?? str(h.outcome) ?? "worked on",
        why: str(h.summary), quote: str(h.note),
      });
    } else if (str(h.decision)) {
      out.push({ at, where: "", kind: "run", what: String(h.decision), why: null, quote: null });
    }
  }
  return out.reverse();
}

/** The latest draft change after the item's last run: "updated since it was last worked on". */
export function recentUpdate(history: Record<string, unknown>[]): ItemEvent | null {
  const events = itemEvents(history);
  const first = events[0];
  return first && first.kind === "draft" && first.what !== "created" ? first : null;
}
