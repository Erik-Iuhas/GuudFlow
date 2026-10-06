import type { ProjectSeats } from "../../types";
import type { CheckState, DecisionWire, DraftWire, PlanWire, RequirementWire } from "../plan/api";
import type { BacklogItemWire, RunBranchWire } from "./api";
import { groupBacklog } from "./model";

export interface Lanes {
  upNext: BacklogItemWire[];
  inSession: BacklogItemWire[];
  waiting: BacklogItemWire[];
  /** The last few finished items, newest first. */
  done: BacklogItemWire[];
  moreDone: number;
}

/** The Overview lanes (storyboard screen 3): open, in a session, waiting for the owner, done. */
export function laneItems(items: readonly BacklogItemWire[], doneShown = 4): Lanes {
  const g = groupBacklog(items);
  const doneAll = [...g.done].sort((a, b) => b.updated_at - a.updated_at);
  return {
    upNext: g.open,
    inSession: g.inRun.filter((i) => !i.pending),
    waiting: g.inRun.filter((i) => i.pending),
    done: doneAll.slice(0, doneShown),
    moreDone: Math.max(0, doneAll.length - doneShown),
  };
}

const SIZE_WORDS: Record<string, string> = { XS: "Tiny", S: "Small", M: "Medium", L: "Large", XL: "Very large" };

/** The last note an earlier session left on an item, if its history has one. */
export function lastSummary(item: BacklogItemWire): string {
  for (const h of [...(item.history ?? [])].reverse()) {
    for (const key of ["summary", "note"]) {
      const v = h[key];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
  }
  return "";
}

/** One plain line under a card in Overview: a size, a state or the last summary. No ids. */
export function cardSubtext(item: BacklogItemWire): string {
  if (item.status === "done") return lastSummary(item) || "Finished";
  if (item.status === "in_run") {
    if (item.pending === "done") return "Finished, waiting for your review";
    if (item.pending === "partial") return "Partly done, waiting for your review";
    if (item.pending === "not_started") return "Not started, waiting for your review";
    return "Being worked on in a session";
  }
  return `${SIZE_WORDS[item.size.toUpperCase()] ?? item.size} job`;
}

const WEEK_S = 7 * 24 * 3600;

interface HistoryEntry { at?: number; summary?: unknown; change?: unknown; op?: unknown; text?: unknown; reason?: unknown }

function entryText(e: HistoryEntry): string {
  for (const v of [e.summary, e.text, e.change, e.op]) if (typeof v === "string" && v.trim()) return v.replace(/_/g, " ").trim();
  return "changed";
}

/** Requirement and decision history entries from the last 7 days, newest first, in plain text. */
export function weekChanges(
  spec: { requirements?: (RequirementWire & { history?: HistoryEntry[] })[]; decisions?: (DecisionWire & { history?: HistoryEntry[] })[] } | undefined,
  nowS: number = Date.now() / 1000,
): { at: number; text: string }[] {
  const out: { at: number; text: string }[] = [];
  const add = (label: string, title: string, history: HistoryEntry[] | undefined) => {
    for (const e of history ?? []) {
      if (typeof e.at !== "number" || nowS - e.at > WEEK_S) continue;
      out.push({ at: e.at, text: `${label} "${title}": ${entryText(e)}` });
    }
  };
  for (const r of spec?.requirements ?? []) add("Requirement", r.text, r.history);
  for (const d of spec?.decisions ?? []) add("Decision", d.title, d.history);
  return out.sort((a, b) => b.at - a.at);
}

/** An unanswered question from the project manager: the last plan message, with no draft open. */
export function planQuestion(plan: PlanWire | undefined): string | null {
  if (!plan || plan.draft?.status === "open") return null;
  const last = plan.messages[plan.messages.length - 1];
  if (!last || last.author !== "pm") return null;
  const text = last.text.trim();
  return text.endsWith("?") ? text : null;
}

/** Draft changes that are decisions and still undecided. */
export function undecidedDecisions(draft: DraftWire | null | undefined): number {
  if (!draft || draft.status !== "open") return 0;
  return draft.changes.filter((c) => c.accepted === null && c.change.op.includes("decision")).length;
}

export interface Routed {
  reviewer: string[] | null;
  lead: string[] | null;
  owner: string[];
}

/** What waits on each seat. A group is null when the user does not hold that seat; Owner always shows. */
export function routedToYou(
  seats: ProjectSeats | undefined,
  pendingRuns: readonly RunBranchWire[],
  plan: PlanWire | undefined,
  pmName = "Jack",
): Routed {
  const cosigns = pendingRuns.filter((r) => r.needs_cosign && !r.cosign).map((r) => `Co-sign the code of ${r.run_id}`);
  const undecided = undecidedDecisions(plan?.draft);
  const q = planQuestion(plan);
  const draftOpen = plan?.draft?.status === "open";
  const owner = [
    ...pendingRuns.map((r) => `Accept or reject ${r.run_id}`),
    ...(draftOpen ? ["A plan draft waits for your approval"] : []),
    ...(q ? [`${pmName} asks: ${q}`] : []),
  ];
  return {
    reviewer: seats?.reviewer && seats.reviewer !== "off" ? cosigns : null,
    lead: seats?.lead ? (undecided ? [`${undecided} draft decision${undecided === 1 ? "" : "s"} undecided`] : []) : null,
    owner,
  };
}

/** An acceptance line that points at a spec check: "R4.2: login works" gives its id and the rest. */
export function acceptanceRef(line: string): { id: string; rest: string } | null {
  const m = /^\s*(R\d+\.\d+)\s*:\s*(.*)$/s.exec(line);
  return m ? { id: m[1]!, rest: m[2]! } : null;
}

/** The state of each check id, from the approved spec. */
export function checkStates(spec: { requirements?: RequirementWire[] } | undefined): Record<string, CheckState> {
  const out: Record<string, CheckState> = {};
  for (const r of spec?.requirements ?? []) for (const c of r.checks ?? []) out[c.id] = c.state;
  return out;
}
