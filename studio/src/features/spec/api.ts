import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { getToken } from "../../api";
import { planKeys, type CheckState, type DecisionWire, type RequirementWire, type RuleWire } from "../plan/api";
import { workKeys } from "../work/api";

// The spec as the team meets it: the slice a run follows, rule adherence, the
// reviewer's co-sign and rule edits (docs/PROJECT_FLOW_PLAN.md §6, §7).

export interface SliceRequirement extends RequirementWire {
  served_by: string[];
}

export interface SliceRule extends RuleWire {
  verdicts?: { met: number; unmet: number };
}

export interface SpecSlice {
  project_id: string;
  requirements: SliceRequirement[];
  decisions: DecisionWire[];
  rules: SliceRule[];
  /** The latest state of each check, keyed by check id ("R4.2"). */
  checks: Record<string, { state: CheckState; run_id: string | null }>;
}

export interface AdherenceRun {
  run_id: string;
  rule_verdicts?: unknown;
  /** A count, or the list of unmet verdicts. */
  unmet?: number | unknown[];
  fixed?: number | unknown[];
}

export interface Adherence {
  runs: AdherenceRun[];
  unmet_merged: number | unknown[];
}

export type CommentSeverity = "note" | "should_fix" | "blocking";

export interface CosignComment {
  file?: string;
  line?: number;
  text: string;
  severity: CommentSeverity;
}

export type CosignVerdict = "cosign" | "changes";

export type RuleEdit =
  | { op: "add_rule"; text: string; applies_to: string[]; strength: "binding" | "preference" }
  | { op: "drop_rule"; id: string; reason: string };

export class SpecApiError extends Error {
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
    throw new SpecApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const specKeys = {
  slice: (runId: string) => ["spec-slice", runId] as const,
  adherence: (projectId: string) => ["adherence", projectId] as const,
};

export const specApi = {
  slice: (runId: string) => send<SpecSlice>("GET", `/api/runs/${enc(runId)}/spec-slice`),
  adherence: (projectId: string, last = 3) =>
    send<Adherence>("GET", `/api/projects/${enc(projectId)}/adherence?last=${last}`),
  cosign: (projectId: string, runId: string, verdict: CosignVerdict, comments: CosignComment[]) =>
    send<unknown>("POST", `/api/projects/${enc(projectId)}/runs-review/${enc(runId)}/cosign`, { verdict, comments }),
  afterSession: (projectId: string, runId: string) =>
    send<unknown>("POST", `/api/projects/${enc(projectId)}/plan/after-session/${enc(runId)}`, {}),
  edit: (projectId: string, change: RuleEdit) =>
    send<{ version: number }>("POST", `/api/projects/${enc(projectId)}/spec/edit`, { change }),
};

/** The spec slice a run follows; polled when `pollMs` is given. */
export function useSpecSlice(runId: string, pollMs: number | false = false): UseQueryResult<SpecSlice> {
  return useQuery({
    queryKey: specKeys.slice(runId),
    queryFn: () => specApi.slice(runId),
    refetchInterval: pollMs,
    retry: false,
  });
}

export function useAdherence(projectId: string, last = 3): UseQueryResult<Adherence> {
  return useQuery({
    queryKey: specKeys.adherence(projectId),
    queryFn: () => specApi.adherence(projectId, last),
    retry: false,
  });
}

export function useCosign(
  projectId: string,
): UseMutationResult<unknown, Error, { runId: string; verdict: CosignVerdict; comments: CosignComment[] }> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v) => specApi.cosign(projectId, v.runId, v.verdict, v.comments),
    onSuccess: () => qc.invalidateQueries({ queryKey: workKeys.runs(projectId) }),
  });
}

export function useRuleEdit(projectId: string): UseMutationResult<{ version: number }, Error, RuleEdit> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (change) => specApi.edit(projectId, change),
    onSuccess: () => qc.invalidateQueries({ queryKey: planKeys.spec(projectId) }),
  });
}

/** A count that the server may send as a number or as a list. */
export function countOf(v: number | unknown[] | undefined): number {
  if (Array.isArray(v)) return v.length;
  return typeof v === "number" ? v : 0;
}

/** "3 rule issues raised, 2 fixed in review. 1 merged with a broken binding rule." */
export function adherenceText(a: Adherence): string {
  const runs = a.runs ?? [];
  const raised = runs.reduce((n, r) => n + countOf(r.unmet), 0);
  const fixed = runs.reduce((n, r) => n + countOf(r.fixed), 0);
  const merged = countOf(a.unmet_merged);
  const issues = raised === 1 ? "1 rule issue raised" : `${raised} rule issues raised`;
  return `${issues}, ${fixed} fixed in review. ${merged} merged with a broken binding rule.`;
}

/** Checks met and total for one requirement, using the slice's latest check states. */
export function checkProgress(req: RequirementWire, slice?: Pick<SpecSlice, "checks">): { met: number; total: number } {
  const states = (req.checks ?? []).map((c) => slice?.checks?.[c.id]?.state ?? c.state);
  return { met: states.filter((s) => s === "met").length, total: states.length };
}
