import { createStore, type StoreApi } from "zustand/vanilla";
import {
  JSON_DETAIL_TYPES,
  type ArtifactDetail,
  type CapabilitiesDetail,
  type OverseerInterventionDetail,
  type RawSignal,
  type ScreenshotReadyDetail,
  type SignalType,
  type TaskColumn,
  type TaskThreadDetail,
  type TypedSignal,
  type WebRequestDetail,
} from "../lib/runEvents.gen";
import { parseApprovalDetail } from "../lib/approvalDetail";
import type { CardWire } from "../types";

// The typed run-event store (docs/STUDIO_UI_SPEC.md section 3). One instance per open
// run, fed by the run websocket. `ingest` is the only writer for signal-driven state;
// `seedCards` is the one extra writer, for the REST card list (it never overwrites
// state that signals already moved). Derived slices update incrementally per signal.

export type AgentState = "idle" | "running" | "complete" | "failed" | "blocked" | "waiting";

export interface AgentView {
  role: string;
  state: AgentState;
  /** Task id of the card the agent is working (set from TASK_MOVED into in_progress). */
  card?: string;
  /** Summary of the agent's last AGENT_RESULT. */
  lastResult?: string;
  /** Id of the agent's last AGENT_RESULT signal (look it up in `byId` for the full result). */
  lastResultSignalId?: string;
  capabilities?: CapabilitiesDetail;
  /** How many times the agent was started. */
  spawns: number;
  toolCalls: number;
  /** USD, summed from AGENT_RESULT.cost. */
  cost: number;
}

export interface CardView {
  id: string;
  title: string;
  owner: string;
  column: TaskColumn;
  kind: string;
  size: string;
  priority: number;
  round: number;
  acceptance: string[];
  thread: TaskThreadDetail[];
  dependsOn: string[];
  since?: string;
  /** True while the card only came from a TASK_ASSIGNED signal (REST has not filled it in). */
  stub?: boolean;
}

export interface ApprovalView {
  /** The approval id used by `POST /api/runs/{id}/approval/{id}`. */
  id: string;
  signalId: string;
  kind: string;
  summary: string;
  role: string | null;
  estimatedCostUsd: number | null;
  timestamp: string;
  /** The original signal, for components that still take a raw Signal. */
  signal: RawSignal;
}

export interface InterventionView {
  signalId: string;
  data: OverseerInterventionDetail;
  summary: string;
  timestamp: string;
  /** Summary of the overseer's AGENT_RESULT that followed, when there was one. */
  result?: string;
}

export interface ArtifactView {
  signalId: string;
  source: "artifact" | "screenshot";
  path: string;
  kind: string;
  role: string | null;
  taskId: string | null;
  size: number | null;
  servedPath: string | null;
  url: string | null;
  createdAt: string;
  /** SCREENSHOT_READY from `request_visual_feedback`: the id the answer must carry. */
  requestId?: string | null;
  /** The question the requesting agent asked about the screenshot. */
  question?: string | null;
}

export interface WebRequestView {
  signalId: string;
  role: string | null;
  tool: string;
  query: string | null;
  url: string | null;
  finalUrl: string | null;
  status: number | string | null;
  cacheHit: boolean;
  bytes: number;
  refused: string | null;
  timestamp: string;
}

/** Reviewer -> author rework round, from REJECTED signals (the review edge's round counter). */
export interface ReviewRoundView {
  reviewer: string;
  author: string;
  /** Highest round seen on this edge. */
  round: number;
  taskId?: string;
}

/** A role that reported BLOCKED on another role (a dashed arrow in the pipeline graph). */
export interface BlockedOnView {
  role: string;
  on: string;
  need?: string;
  taskId?: string;
  since: string;
}

export interface HumanNeededView {
  signalId: string;
  role: string | null;
  summary: string;
  timestamp: string;
}

export interface RunState {
  runId: string;
  /** `snapshot.live` from the websocket: false once the run has finished. */
  live: boolean;
  /** Append-only, in arrival order, deduped by id. */
  signals: TypedSignal[];
  /**
   * Id index over `signals`. Mutated in place (stable identity, so appending stays O(1));
   * subscribe to `signals` or a derived slice for reactivity, not to this map.
   */
  byId: Map<string, TypedSignal>;
  agents: Record<string, AgentView>;
  cards: Record<string, CardView>;
  approvals: ApprovalView[];
  interventions: InterventionView[];
  /** role -> USD, from AGENT_RESULT.cost. */
  costs: Record<string, number>;
  artifacts: ArtifactView[];
  web: WebRequestView[];
  /** role -> TOOL_CALL signals, in order: the lane feed. */
  toolFeed: Record<string, TypedSignal[]>;
  /** `"{reviewer}>{author}"` -> highest REJECTED round on that review edge. */
  reviews: Record<string, ReviewRoundView>;
  /** role -> its open BLOCKED-on-another-role report; cleared when the role starts or ends again. */
  blockedOn: Record<string, BlockedOnView>;
  /** The latest unanswered HUMAN_NEEDED; drives the banner and comment-box focus. */
  humanNeeded: HumanNeededView | null;
  ingest(raw: RawSignal | RawSignal[]): void;
  seedCards(cards: readonly CardWire[]): void;
  setLive(live: boolean): void;
  reset(): void;
}

export type RunStore = StoreApi<RunState>;

type Data<T extends SignalType> = Extract<TypedSignal, { type: T }>["data"];

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Parse `detail` once. Only JSON-detail kinds are parsed; anything malformed becomes null. */
function parseDetail(raw: RawSignal): unknown {
  if (!JSON_DETAIL_TYPES.has(raw.type) || !raw.detail) return null;
  try {
    const parsed: unknown = JSON.parse(raw.detail);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function toTyped(raw: RawSignal): TypedSignal {
  // One documented cast: `data` is only non-null for JSON_DETAIL_TYPES, and its shape is the
  // generated detail interface for that type. Handlers below re-check the fields they use.
  return { ...raw, data: parseDetail(raw) } as TypedSignal;
}

function emptyAgent(role: string): AgentView {
  return { role, state: "idle", spawns: 0, toolCalls: 0, cost: 0 };
}

const TASK_ASSIGNED_RE = /^task:(\S+) → (\S+?): (.*)$/s;

/** Copy-on-first-write view of the state for one `ingest` call. */
interface Draft {
  signals: TypedSignal[];
  agents: Record<string, AgentView>;
  cards: Record<string, CardView>;
  approvals: ApprovalView[];
  interventions: InterventionView[];
  costs: Record<string, number>;
  artifacts: ArtifactView[];
  web: WebRequestView[];
  toolFeed: Record<string, TypedSignal[]>;
  reviews: Record<string, ReviewRoundView>;
  blockedOn: Record<string, BlockedOnView>;
  humanNeeded: HumanNeededView | null;
  dirty: Set<keyof Draft>;
}

function startDraft(s: RunState): Draft {
  return {
    signals: s.signals, agents: s.agents, cards: s.cards, approvals: s.approvals,
    interventions: s.interventions, costs: s.costs, artifacts: s.artifacts, web: s.web,
    toolFeed: s.toolFeed, reviews: s.reviews, blockedOn: s.blockedOn,
    humanNeeded: s.humanNeeded, dirty: new Set(),
  };
}

/** Make slice `k` writable (shallow copy once per ingest call). */
function own<K extends Exclude<keyof Draft, "dirty" | "humanNeeded">>(d: Draft, k: K): Draft[K] {
  if (!d.dirty.has(k)) {
    const cur = d[k];
    (d as unknown as Record<string, unknown>)[k] = Array.isArray(cur) ? [...cur] : { ...cur };
    d.dirty.add(k);
  }
  return d[k];
}

function patchAgent(d: Draft, role: string, patch: (a: AgentView) => AgentView): void {
  const agents = own(d, "agents");
  agents[role] = patch(agents[role] ?? emptyAgent(role));
}

function clearBlockedOn(d: Draft, role: string): void {
  if (!d.blockedOn[role]) return;
  delete own(d, "blockedOn")[role];
}

function patchCard(d: Draft, id: string, patch: (c: CardView) => CardView): void {
  const cards = own(d, "cards");
  const existing = cards[id];
  if (existing) cards[id] = patch(existing);
}

export function createRunStore(runId: string): RunStore {
  // Approvals that were decided before (or without) their APPROVAL_NEEDED being seen.
  let decided = new Set<string>();

  function initial(): Omit<RunState, "ingest" | "seedCards" | "setLive" | "reset"> {
    return {
      runId, live: true, signals: [], byId: new Map(), agents: {}, cards: {}, approvals: [],
      interventions: [], costs: {}, artifacts: [], web: [], toolFeed: {}, reviews: {},
      blockedOn: {}, humanNeeded: null,
    };
  }

  function apply(d: Draft, sig: TypedSignal): void {
    const role = sig.from_agent;
    switch (sig.type) {
      case "STARTED":
        if (role) {
          patchAgent(d, role, (a) => ({ ...a, state: "running", spawns: a.spawns + 1 }));
          clearBlockedOn(d, role);
        }
        break;
      case "COMPLETE":
        if (role) {
          patchAgent(d, role, (a) => ({ ...a, state: "complete" }));
          clearBlockedOn(d, role);
        }
        break;
      case "FAILED":
        if (role) {
          patchAgent(d, role, (a) => ({ ...a, state: "failed" }));
          clearBlockedOn(d, role);
        }
        break;
      case "BLOCKED": {
        if (!role) break;
        patchAgent(d, role, (a) => ({ ...a, state: "blocked" }));
        const data: Data<"BLOCKED"> = sig.data;
        if (data && typeof data.on === "string" && data.on) {
          own(d, "blockedOn")[role] = {
            role, on: data.on, need: data.need, taskId: data.task_id, since: sig.timestamp,
          };
        }
        break;
      }
      case "REJECTED": {
        const data: Data<"REJECTED"> = sig.data;
        if (!role || !data || !Array.isArray(data.authors)) break;
        const round = typeof data.round === "number" ? data.round : 1;
        for (const author of data.authors) {
          if (typeof author !== "string") continue;
          const key = `${role}>${author}`;
          const prev = d.reviews[key];
          if (prev && prev.round >= round) continue;
          own(d, "reviews")[key] = { reviewer: role, author, round, taskId: data.task_id };
        }
        break;
      }
      case "CAPABILITIES": {
        const data: Data<"CAPABILITIES"> = sig.data;
        if (role && data) patchAgent(d, role, (a) => ({ ...a, capabilities: data }));
        break;
      }
      case "TOOL_CALL": {
        if (!role) break;
        patchAgent(d, role, (a) => ({ ...a, toolCalls: a.toolCalls + 1 }));
        const feed = own(d, "toolFeed");
        feed[role] = [...(feed[role] ?? []), sig];
        break;
      }
      case "AGENT_RESULT": {
        const data: Data<"AGENT_RESULT"> = sig.data;
        const cost = typeof data?.cost === "number" && Number.isFinite(data.cost) ? data.cost : 0;
        if (role) {
          patchAgent(d, role, (a) => ({
            ...a, lastResult: sig.summary, lastResultSignalId: sig.id, cost: a.cost + cost,
          }));
          if (cost) {
            const costs = own(d, "costs");
            costs[role] = (costs[role] ?? 0) + cost;
          }
          // The overseer's result belongs with the intervention it just made.
          if (role === "overseer") {
            const last = d.interventions[d.interventions.length - 1];
            if (last && last.result === undefined) {
              const list = own(d, "interventions");
              list[list.length - 1] = { ...last, result: sig.summary };
            }
          }
        }
        break;
      }
      case "TASK_ASSIGNED": {
        // detail is the bare task id; the summary reads "task:{id} → {owner}: {goal}".
        const id = sig.detail;
        if (!id || d.cards[id]) break;
        const m = TASK_ASSIGNED_RE.exec(sig.summary);
        const cards = own(d, "cards");
        cards[id] = {
          id, title: m?.[3] ?? sig.summary, owner: m?.[2] ?? role ?? "", column: "todo",
          kind: "", size: "", priority: 0, round: 0, acceptance: [], thread: [], dependsOn: [],
          stub: true,
        };
        break;
      }
      case "TASK_MOVED": {
        const data: Data<"TASK_MOVED"> = sig.data;
        if (!data || typeof data.task_id !== "string" || typeof data.to !== "string") break;
        if (!d.cards[data.task_id]) {
          const cards = own(d, "cards");
          cards[data.task_id] = {
            id: data.task_id, title: sig.summary, owner: data.owner ?? "", column: data.to,
            kind: "", size: "", priority: 0, round: data.round ?? 0, acceptance: [], thread: [],
            dependsOn: [], since: sig.timestamp, stub: true,
          };
        } else {
          patchCard(d, data.task_id, (c) => ({
            ...c, column: data.to, round: data.round ?? c.round, owner: data.owner || c.owner,
            since: sig.timestamp,
          }));
        }
        if (data.owner) {
          if (data.to === "in_progress") {
            patchAgent(d, data.owner, (a) => ({ ...a, card: data.task_id }));
          } else if (d.agents[data.owner]?.card === data.task_id) {
            patchAgent(d, data.owner, (a) => ({ ...a, card: undefined }));
          }
        }
        break;
      }
      case "TASK_THREAD": {
        const data: Data<"TASK_THREAD"> = sig.data;
        if (!data || typeof data.task_id !== "string") break;
        patchCard(d, data.task_id, (c) => ({ ...c, thread: [...c.thread, data] }));
        break;
      }
      case "APPROVAL_NEEDED": {
        const ctx = parseApprovalDetail(sig.detail);
        if (!ctx || decided.has(ctx.approval_id)) break;
        const approvals = own(d, "approvals");
        if (approvals.some((a) => a.id === ctx.approval_id)) break;
        approvals.push({
          id: ctx.approval_id, signalId: sig.id, kind: ctx.kind, summary: sig.summary,
          role: role ?? null, estimatedCostUsd: ctx.estimated_cost_usd ?? null,
          timestamp: sig.timestamp, signal: sig,
        });
        break;
      }
      case "APPROVAL_GRANTED":
      case "APPROVAL_DENIED": {
        const ctx = parseApprovalDetail(sig.detail);
        if (!ctx) break;
        decided.add(ctx.approval_id);
        if (d.approvals.some((a) => a.id === ctx.approval_id)) {
          const approvals = own(d, "approvals");
          const i = approvals.findIndex((a) => a.id === ctx.approval_id);
          approvals.splice(i, 1);
        }
        break;
      }
      case "OVERSEER_INTERVENTION": {
        const data: Data<"OVERSEER_INTERVENTION"> = sig.data;
        if (!data) break;
        own(d, "interventions").push({
          signalId: sig.id, data, summary: sig.summary, timestamp: sig.timestamp,
        });
        break;
      }
      case "ARTIFACT": {
        const data: Data<"ARTIFACT"> = sig.data;
        if (!data || typeof data.path !== "string") break;
        own(d, "artifacts").push(artifactFromDetail(sig.id, data));
        break;
      }
      case "SCREENSHOT_READY": {
        const data: Data<"SCREENSHOT_READY"> = sig.data;
        if (!data || typeof data.path !== "string") break;
        own(d, "artifacts").push(screenshotFromDetail(sig.id, data, role, sig.timestamp));
        break;
      }
      case "WEB_REQUEST": {
        const data: Data<"WEB_REQUEST"> = sig.data;
        if (!data || typeof data.tool !== "string") break;
        own(d, "web").push(webFromDetail(sig, data));
        break;
      }
      case "HUMAN_NEEDED":
        d.humanNeeded = { signalId: sig.id, role: role ?? null, summary: sig.summary, timestamp: sig.timestamp };
        if (role) patchAgent(d, role, (a) => ({ ...a, state: "waiting" }));
        break;
      case "HUMAN_RESPONDED":
        d.humanNeeded = null;
        for (const a of Object.values(d.agents)) {
          if (a.state === "waiting") patchAgent(d, a.role, (x) => ({ ...x, state: "running" }));
        }
        break;
      default:
        break;
    }
  }

  return createStore<RunState>((set, get) => ({
    ...initial(),

    ingest(raw) {
      const list = Array.isArray(raw) ? raw : [raw];
      const state = get();
      let d: Draft | null = null;
      for (const r of list) {
        if (!r || typeof r.id !== "string" || state.byId.has(r.id)) continue;
        const typed = toTyped(r);
        state.byId.set(r.id, typed);
        d ??= startDraft(state);
        own(d, "signals").push(typed);
        apply(d, typed);
      }
      if (!d) return;
      const patch: Partial<RunState> = {};
      for (const k of d.dirty) (patch as Record<string, unknown>)[k] = d[k];
      patch.humanNeeded = d.humanNeeded;
      set(patch);
    },

    seedCards(cards) {
      const current = get().cards;
      const next = { ...current };
      for (const w of cards) {
        const prev = current[w.id];
        const fromRest: CardView = {
          id: w.id, title: w.title, owner: w.owner, column: w.column, kind: w.kind ?? "",
          size: w.size ?? "", priority: w.priority ?? 0, round: w.review_round ?? 0,
          acceptance: w.acceptance ?? [], thread: w.thread ?? [], dependsOn: w.depends_on ?? [],
          since: w.column_since ?? undefined,
        };
        // Live state wins: a card that signals already moved keeps its column/round/thread.
        next[w.id] = prev && !prev.stub
          ? { ...fromRest, column: prev.column, round: prev.round, since: prev.since, thread: prev.thread }
          : fromRest;
      }
      set({ cards: next });
    },

    setLive(live) {
      if (get().live !== live) set({ live });
    },

    reset() {
      decided = new Set();
      set(initial());
    },
  }));
}

function artifactFromDetail(signalId: string, d: ArtifactDetail): ArtifactView {
  return {
    signalId, source: "artifact", path: d.path, kind: d.kind ?? "file", role: d.role ?? null,
    taskId: d.task_id ?? null, size: typeof d.size === "number" ? d.size : null,
    servedPath: d.served_path ?? null, url: null, createdAt: d.created_at ?? "",
  };
}

function screenshotFromDetail(
  signalId: string, d: ScreenshotReadyDetail, role: string | null, timestamp: string,
): ArtifactView {
  return {
    signalId, source: "screenshot", path: d.path, kind: "screenshot", role: d.role ?? role,
    taskId: null, size: null, servedPath: null, url: d.url ?? null, createdAt: timestamp,
    requestId: d.request_id ?? null, question: d.question ?? null,
  };
}

function webFromDetail(sig: TypedSignal, d: WebRequestDetail): WebRequestView {
  return {
    signalId: sig.id, role: sig.from_agent, tool: d.tool, query: d.query ?? null, url: d.url ?? null,
    finalUrl: d.final_url ?? null, status: d.status ?? null, cacheHit: Boolean(d.cache_hit),
    bytes: typeof d.bytes === "number" ? d.bytes : 0, refused: d.refused ?? null,
    timestamp: sig.timestamp,
  };
}
