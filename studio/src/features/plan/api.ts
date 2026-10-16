import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { getToken } from "../../api";
import { workKeys } from "../work/api";

// The project manager's plan conversation, its open draft and the approved spec
// (docs/PROJECT_FLOW_PLAN.md §3, §6).

export type CheckState = "unknown" | "met" | "unmet";
export type PlanMode = "update" | "discuss";

export type CheckMethod = "unit" | "manual" | "visual" | "perf";

export interface CheckWire {
  id: string;
  text: string;
  state: CheckState;
  run_id: string | null;
  method?: CheckMethod | null;
  given?: string;
  test?: string | null;
}

export type ParamValue = string | number | boolean;

export interface ParamWire {
  key: string;
  value: ParamValue;
  unit?: string;
  assumed?: boolean;
  note?: string;
}

/** A structured check in a draft change (core/plan_drafts.py CheckSpec). */
export interface CheckSpecWire {
  text: string;
  method?: CheckMethod | null;
  given?: string;
  test?: string | null;
}

export interface RequirementWire {
  id: string;
  /** Short name; older rows get one derived from the text. */
  title?: string;
  text: string;
  params?: ParamWire[];
  rationale?: string;
  pinned_fields?: string[];
  kind: string;
  priority: string;
  checks: CheckWire[];
  assumed: boolean;
  experiment: boolean;
  status: "active" | "dropped";
  source: string;
  version: number;
  /** Where it came from: the draft and the reason it was added for. */
  origin?: HistoryStamp | null;
  /** Earlier versions, each with the draft that changed it and why. */
  history?: (HistoryStamp & {
    version?: number; title?: string; text?: string; params?: ParamWire[];
    /** Rows a session wrote when it was accepted: the check states it set. */
    check_states?: Record<string, CheckState>;
  })[];
}

/** Which draft (or session) changed something, when, and why. */
export interface HistoryStamp {
  draft?: string;
  at?: number;
  by?: string;
  why?: ReasonWire;
}

export interface DecisionWire {
  id: string;
  title: string;
  choice: string;
  reason: string;
  serves: string[];
  binding: boolean;
  status: "accepted" | "superseded" | "dropped";
  decided_by: string;
  supersedes: string | null;
  version: number;
}

export interface RuleWire {
  id: string;
  text: string;
  source: string;
  applies_to: string[];
  strength: "binding" | "preference";
  status: "active" | "dropped";
}

export interface SpecWire {
  version: number;
  requirements: RequirementWire[];
  decisions: DecisionWire[];
  rules: RuleWire[];
  /** What the project is built with (worker context plan B1); fields a person set are pinned. */
  stack?: StackWire;
}

export const STACK_FIELDS = [
  ["language", "Language"],
  ["framework", "Framework and libraries"],
  ["package_manager", "Packages"],
  ["test_command", "Test command"],
  ["run_command", "Run command"],
  ["source_dir", "Source folder"],
  ["test_dir", "Test folder"],
  ["notes", "Also"],
] as const;
export type StackField = (typeof STACK_FIELDS)[number][0];

export type StackWire = Partial<Record<StackField, string>> & {
  pinned_fields?: string[];
  set_by?: string;
};

/** One proposed change; the fields depend on `op` (see core/plan_drafts.py). */
export interface ChangeWire {
  op: string;
  id?: string;
  ref?: string;
  /** Theme label; changes with the same group can be ticked together. */
  group?: string | null;
  // Field-level requirement ops (set_param, add_check, edit_check, ...).
  key?: string;
  value?: ParamValue;
  unit?: string | null;
  note?: string | null;
  check?: CheckSpecWire;
  method?: CheckMethod | null;
  given?: string | null;
  test?: string | null;
  params?: ParamWire[];
  rationale?: string;
  text?: string;
  title?: string;
  choice?: string;
  reason?: string;
  kind?: string;
  priority?: string | number;
  size?: string;
  effort?: string;
  checks?: (string | CheckSpecWire)[];
  acceptance?: string[];
  serves?: string[];
  depends_on?: string[];
  assumed?: boolean;
  experiment?: boolean;
  binding?: boolean;
  applies_to?: string[];
  strength?: string;
  notes?: string;
  // Team ops (add_role, edit_role, remove_role).
  role?: string;
  role_type?: string;
  description?: string;
  model_tier?: string;
  review_of?: string[];
  max_review_rounds?: number;
  name?: string;
  capabilities?: Record<string, string>;
  contract?: string;
  // Project ops (set_kind uses `kind` above; add_contract_rule; role contracts plan §3, §6).
  start_mode?: string;
  rule?: string;
  evidence_runs?: string[];
  // set_stack (worker context plan B1); `notes` is above.
  language?: string;
  framework?: string;
  package_manager?: string;
  test_command?: string;
  run_command?: string;
  source_dir?: string;
  test_dir?: string;
}

export interface DraftChangeWire {
  cid: string;
  change: ChangeWire;
  accepted: boolean | null;
  by: string;
  touched: boolean;
  /** Style problems the project manager's change has; a person decides. */
  warnings?: string[];
}

/** Why a set of draft changes is proposed (core/plan_drafts.py ChangeGroup). */
export interface ReasonWire {
  name: string;
  /** What changes, in plain words. */
  summary: string;
  because: string;
  source?: "person" | "session" | "pm";
  /** The exact words that asked for it. */
  quote?: string | null;
}

export interface DraftWire {
  id: string;
  base_version: number;
  status: "open" | "approved" | "discarded";
  changes: DraftChangeWire[];
  /** Reasons by name; each change's `group` names one. Older drafts have none. */
  groups?: Record<string, ReasonWire>;
}

export interface PlanMessageWire {
  id: number;
  at: number;
  /** `tech_lead`: a feasibility note the tech lead left while planning (role contracts plan §2.2). */
  author: "human" | "pm" | "tech_lead";
  text: string;
  draft_id: string | null;
  mode: string;
  cost_usd?: number;
  /** A PM turn: complete, partial (some changes left out) or failed (nothing usable). */
  status?: "" | "complete" | "partial" | "failed";
  /** Why changes were left out of the draft (a partial or failed turn). */
  problems?: string[];
  /** Changes the turn left out, each with the fix GuudFlow computed (when there is one). */
  parked?: ParkedChangeWire[];
}

export interface ParkedChangeWire {
  id: number;
  code: string;
  problem: string;
  change: ChangeWire;
  suggestion: { change: ChangeWire; label: string } | null;
}

export interface PlanWire {
  messages: PlanMessageWire[];
  draft: DraftWire | null;
  /** What the project manager's calls cost (all time, last 24 h, and calls that bought nothing). */
  pm_spend?: PmSpendWire;
}

export interface PmSpendWire {
  calls: number;
  total_usd: number;
  last_24h_usd: number;
  wasted_usd: number;
}

export interface PlanTurnWire {
  messages: PlanMessageWire[];
  draft: DraftWire | null;
}

export interface ApproveResult {
  version: number;
  applied: string[];
  created: Record<string, string>;
  /** Ticked team ops, already applied to the project (`team` says which roles). */
  team_changes?: ChangeWire[];
  team?: { added: string[]; edited: string[]; removed: string[] };
}

/** An HTTP error carrying the server's `detail`. */
export class PlanApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const enc = encodeURIComponent;

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new PlanApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const planKeys = {
  spec: (projectId: string) => ["spec", projectId] as const,
  plan: (projectId: string) => ["plan", projectId] as const,
  preview: (projectId: string) => ["plan-preview", projectId] as const,
};

/** Changes Jack has finished writing in the turn now running (a preview, not validated). */
export interface PlanPreviewWire {
  active: boolean;
  changes: Record<string, unknown>[];
}

export const planApi = {
  preview: (projectId: string) =>
    send<PlanPreviewWire>("GET", `/api/projects/${enc(projectId)}/plan/preview`),
  spec: (projectId: string) => send<SpecWire>("GET", `/api/projects/${enc(projectId)}/spec`),
  plan: (projectId: string) => send<PlanWire>("GET", `/api/projects/${enc(projectId)}/plan`),
  start: (projectId: string) => send<PlanTurnWire>("POST", `/api/projects/${enc(projectId)}/plan/start`, {}),
  message: (projectId: string, text: string, mode: PlanMode) =>
    send<PlanTurnWire>("POST", `/api/projects/${enc(projectId)}/plan/messages`, { text, mode }),
  tick: (projectId: string, cid: string, accepted: boolean | null) =>
    send<DraftWire>("PATCH", `/api/projects/${enc(projectId)}/plan/draft/${enc(cid)}`, { accepted }),
  approve: (projectId: string) =>
    send<ApproveResult>("POST", `/api/projects/${enc(projectId)}/plan/draft/approve`, {}),
  edit: (projectId: string, change: ChangeWire) =>
    send<ApproveResult>("POST", `/api/projects/${enc(projectId)}/spec/edit`, { change }),
  discard: (projectId: string) =>
    send<Record<string, never>>("POST", `/api/projects/${enc(projectId)}/plan/draft/discard`, {}),
  acceptParked: (projectId: string, messageId: number, parkedId: number) =>
    send<PlanTurnWire>(
      "POST", `/api/projects/${enc(projectId)}/plan/messages/${messageId}/parked/${parkedId}/accept`, {}),
};

export function useSpec(projectId: string, enabled = true): UseQueryResult<SpecWire> {
  return useQuery({ queryKey: planKeys.spec(projectId), queryFn: () => planApi.spec(projectId), enabled: enabled && !!projectId });
}

export function usePlan(projectId: string): UseQueryResult<PlanWire> {
  return useQuery({ queryKey: planKeys.plan(projectId), queryFn: () => planApi.plan(projectId) });
}

/** Folds a turn's new messages and draft into the cached plan. */
function mergeTurn(prev: PlanWire | undefined, turn: PlanTurnWire): PlanWire {
  const have = new Set((prev?.messages ?? []).map((m) => m.id));
  return { messages: [...(prev?.messages ?? []), ...turn.messages.filter((m) => !have.has(m.id))], draft: turn.draft };
}

/** Polls the streamed preview once a second while a turn is running. */
export function usePlanPreview(projectId: string, running: boolean): UseQueryResult<PlanPreviewWire> {
  return useQuery({
    queryKey: planKeys.preview(projectId),
    queryFn: () => planApi.preview(projectId),
    enabled: running && !!projectId,
    refetchInterval: running ? 1000 : false,
  });
}

export function useStartPlan(projectId: string): UseMutationResult<PlanTurnWire, Error, void> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => planApi.start(projectId),
    onSuccess: (turn) => qc.setQueryData<PlanWire>(planKeys.plan(projectId), (p) => mergeTurn(p, turn)),
  });
}

export function useSendMessage(
  projectId: string,
): UseMutationResult<PlanTurnWire, Error, { text: string; mode: PlanMode }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v) => planApi.message(projectId, v.text, v.mode),
    onSuccess: (turn) => qc.setQueryData<PlanWire>(planKeys.plan(projectId), (p) => mergeTurn(p, turn)),
  });
}

/** "Accept the suggestion": a left-out change goes into the draft with its computed fix. */
export function useAcceptParked(
  projectId: string,
): UseMutationResult<PlanTurnWire, Error, { messageId: number; parkedId: number }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v) => planApi.acceptParked(projectId, v.messageId, v.parkedId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: planKeys.plan(projectId) }),
  });
}

export function useTickChange(
  projectId: string,
): UseMutationResult<DraftWire, Error, { cid: string; accepted: boolean | null }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v) => planApi.tick(projectId, v.cid, v.accepted),
    onSuccess: (draft) =>
      qc.setQueryData<PlanWire>(planKeys.plan(projectId), (p) => ({ messages: p?.messages ?? [], draft })),
  });
}

/** Spec, plan and backlog all change when a draft is approved or discarded. */
function invalidateAll(qc: ReturnType<typeof useQueryClient>, projectId: string): void {
  void qc.invalidateQueries({ queryKey: planKeys.spec(projectId) });
  void qc.invalidateQueries({ queryKey: planKeys.plan(projectId) });
  void qc.invalidateQueries({ queryKey: workKeys.backlog(projectId) });
  // An approved team change edits the project doc, so the project config is stale too.
  void qc.invalidateQueries({ queryKey: ["projects", projectId], exact: true });
  void qc.invalidateQueries({ queryKey: ["project", projectId] });
}

export function useApproveDraft(projectId: string): UseMutationResult<ApproveResult, Error, void> {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => planApi.approve(projectId), onSuccess: () => invalidateAll(qc, projectId) });
}

export function useDiscardDraft(projectId: string): UseMutationResult<unknown, Error, void> {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => planApi.discard(projectId), onSuccess: () => invalidateAll(qc, projectId) });
}

/** A person's direct spec edit (applied at once and pinned), e.g. confirming a param. */
export function useEditSpec(projectId: string): UseMutationResult<ApproveResult, Error, ChangeWire> {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (change) => planApi.edit(projectId, change), onSuccess: () => invalidateAll(qc, projectId) });
}
