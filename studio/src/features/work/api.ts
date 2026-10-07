import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { getToken } from "../../api";

// The project as ongoing work (plan §13): its backlog and the runs waiting for review.

export type ItemStatus = "open" | "in_run" | "done" | "dropped";
export type ItemOutcome = "done" | "partial" | "not_started";

export interface BacklogItemWire {
  id: string;
  title: string;
  acceptance: string[];
  kind: string;
  size: string;
  priority: number;
  status: ItemStatus;
  notes: string;
  files_hint: string[];
  source: string;
  created_at: number;
  updated_at: number;
  run_id: string | null;
  pending: ItemOutcome | null;
  history: Record<string, unknown>[];
  depends_on: string[];
  /** Requirement ids this item serves. */
  serves?: string[];
  /** Gateway spend on the item across runs (listing only). */
  total_cost_usd?: number;
}

export type RunState = "pending" | "accepted" | "rejected" | "no_changes" | "superseded";

export interface CheckWire {
  command: string;
  status: "passed" | "failed" | "timed_out" | "skipped";
  exit_code: number | null;
  output: string;
  duration_s: number;
  where: "docker" | "host" | "none";
  reason: string;
}

export interface SyncWire {
  status: "seeded" | "up_to_date" | "fast_forward" | "merged" | "conflict" | "error";
  main: string;
  detail: string;
  at: number;
}

export interface PublishWire {
  status: "skipped" | "pushed" | "pr_opened" | "error";
  ref: string;
  url: string;
  detail: string;
}

export interface RemoteSummary {
  url: string;
  branch: string;
  publish: "none" | "branch" | "pr" | "main";
  token: boolean;
}

export interface AutopilotStatus {
  enabled: boolean;
  last_start?: number | null;
  next_due?: number;
  week_spend_usd?: number;
  max_usd_per_week?: number;
  error?: string;
}

export interface RunsReview {
  main: string | null;
  runs: RunBranchWire[];
  remote?: RemoteSummary | null;
  autopilot?: AutopilotStatus;
}

export interface Proposal {
  title: string;
  acceptance: string[];
  kind: string;
  size: string;
  why: string;
}

export interface CosignWire {
  verdict: "cosign" | "changes";
  by: string;
  at: number;
  comments: { file?: string; line?: number; text: string; severity: "note" | "should_fix" | "blocking" }[];
}

export interface RunBranchWire {
  run_id: string;
  state: RunState;
  base: string;
  head: string;
  commits: number;
  files: string[];
  submitted_at: number;
  decided_at: number | null;
  merged: string;
  note: string;
  parent_run: string | null;
  check?: CheckWire | null;
  sync?: SyncWire | null;
  published?: PublishWire | null;
  /** A reviewer's co-sign is needed before the owner can accept. */
  needs_cosign?: boolean;
  cosign?: CosignWire | null;
}

export interface NewItem {
  title: string;
  acceptance: string[];
  kind: string;
  size: string;
  priority?: number;
  depends_on?: string[];
  serves?: string[];
}

export type ItemPatch = Partial<Pick<BacklogItemWire, "title" | "acceptance" | "kind" | "size" | "priority" | "notes">> & {
  status?: "open" | "dropped";
};

/** An HTTP error with the server's `detail` and, for a conflicting accept, the files. */
export class WorkApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly files: string[] = [],
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
    let detail = res.statusText;
    let files: string[] = [];
    try {
      const j = (await res.json()) as { detail?: unknown; files?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
      if (Array.isArray(j.files)) files = j.files.map(String);
    } catch {
      /* keep the status text */
    }
    throw new WorkApiError(res.status, detail, files);
  }
  return (await res.json()) as T;
}

export const workKeys = {
  backlog: (projectId: string) => ["projects", projectId, "backlog"] as const,
  runs: (projectId: string) => ["projects", projectId, "runs-review"] as const,
  changes: (projectId: string, runId: string) => ["projects", projectId, "runs-review", runId] as const,
};

/** A session that stopped without finishing (host restart), api/routes/project_work.py. */
export interface InterruptedRun {
  run_id: string;
  items: string[];
  cards_done: number;
  cards_total: number;
  last_activity: string;
}

export const workApi = {
  interrupted: (projectId: string) =>
    send<{ runs: InterruptedRun[] }>("GET", `/api/projects/${enc(projectId)}/interrupted`).then((r) => r.runs),
  salvage: (projectId: string, runId: string) =>
    send<{ run_id: string; state: string | null }>(
      "POST", `/api/projects/${enc(projectId)}/interrupted/${enc(runId)}/salvage`, {}),
  backlog: (projectId: string) =>
    send<{ items: BacklogItemWire[] }>("GET", `/api/projects/${enc(projectId)}/backlog`).then((r) => r.items),
  add: (projectId: string, item: NewItem) =>
    send<BacklogItemWire>("POST", `/api/projects/${enc(projectId)}/backlog`, item),
  patch: (projectId: string, itemId: string, patch: ItemPatch) =>
    send<BacklogItemWire>("PATCH", `/api/projects/${enc(projectId)}/backlog/${enc(itemId)}`, patch),
  runs: (projectId: string) => send<RunsReview>("GET", `/api/projects/${enc(projectId)}/runs-review`),
  groom: (projectId: string) =>
    send<{ proposals: Proposal[] }>("POST", `/api/projects/${enc(projectId)}/backlog/groom`, {}).then(
      (r) => r.proposals,
    ),
  syncRemote: (projectId: string) => send<SyncWire>("POST", `/api/projects/${enc(projectId)}/remote/sync`, {}),
  changes: (projectId: string, runId: string) =>
    send<{ diff: string }>("GET", `/api/projects/${enc(projectId)}/runs-review/${enc(runId)}/changes`).then(
      (r) => r.diff,
    ),
  decide: (projectId: string, runId: string, accept: boolean, note: string) =>
    send<{ run: RunBranchWire; items: BacklogItemWire[] }>(
      "POST",
      `/api/projects/${enc(projectId)}/runs-review/${enc(runId)}/${accept ? "accept" : "reject"}`,
      { note },
    ),
};

export function useBacklog(projectId: string): UseQueryResult<BacklogItemWire[]> {
  return useQuery({ queryKey: workKeys.backlog(projectId), queryFn: () => workApi.backlog(projectId) });
}

export function useProjectRuns(projectId: string): UseQueryResult<RunsReview> {
  return useQuery({ queryKey: workKeys.runs(projectId), queryFn: () => workApi.runs(projectId) });
}

export function useRunChanges(projectId: string, runId: string | null): UseQueryResult<string> {
  return useQuery({
    queryKey: workKeys.changes(projectId, runId ?? ""),
    queryFn: () => workApi.changes(projectId, runId as string),
    enabled: runId !== null,
  });
}

function useInvalidate(projectId: string): () => Promise<void> {
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries({ queryKey: ["projects", projectId] });
  };
}

export function useAddItem(projectId: string): UseMutationResult<BacklogItemWire, Error, NewItem> {
  const invalidate = useInvalidate(projectId);
  return useMutation({ mutationFn: (item: NewItem) => workApi.add(projectId, item), onSuccess: invalidate });
}

export function usePatchItem(
  projectId: string,
): UseMutationResult<BacklogItemWire, Error, { itemId: string; patch: ItemPatch }> {
  const invalidate = useInvalidate(projectId);
  return useMutation({
    mutationFn: ({ itemId, patch }) => workApi.patch(projectId, itemId, patch),
    onSuccess: invalidate,
  });
}

export function useGroom(projectId: string): UseMutationResult<Proposal[], Error, void> {
  return useMutation({ mutationFn: () => workApi.groom(projectId) });
}

export function useSyncRemote(projectId: string): UseMutationResult<SyncWire, Error, void> {
  const invalidate = useInvalidate(projectId);
  return useMutation({ mutationFn: () => workApi.syncRemote(projectId), onSuccess: invalidate });
}

export function useDecideRun(
  projectId: string,
): UseMutationResult<unknown, Error, { runId: string; accept: boolean; note: string }> {
  const invalidate = useInvalidate(projectId);
  return useMutation({
    mutationFn: ({ runId, accept, note }) => workApi.decide(projectId, runId, accept, note),
    onSuccess: invalidate,
  });
}

export function useInterrupted(projectId: string): UseQueryResult<InterruptedRun[]> {
  return useQuery({ queryKey: ["projects", projectId, "interrupted"], queryFn: () => workApi.interrupted(projectId) });
}

/** Send an interrupted session's merged work to review (FIELD_NOTES F3). */
export function useSalvage(projectId: string): UseMutationResult<{ run_id: string; state: string | null }, Error, string> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => workApi.salvage(projectId, runId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["projects", projectId] });
    },
  });
}
