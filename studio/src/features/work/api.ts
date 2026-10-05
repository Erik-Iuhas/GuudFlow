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
}

export type RunState = "pending" | "accepted" | "rejected" | "no_changes";

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
}

export interface NewItem {
  title: string;
  acceptance: string[];
  kind: string;
  size: string;
  priority?: number;
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

export const workApi = {
  backlog: (projectId: string) =>
    send<{ items: BacklogItemWire[] }>("GET", `/api/projects/${enc(projectId)}/backlog`).then((r) => r.items),
  add: (projectId: string, item: NewItem) =>
    send<BacklogItemWire>("POST", `/api/projects/${enc(projectId)}/backlog`, item),
  patch: (projectId: string, itemId: string, patch: ItemPatch) =>
    send<BacklogItemWire>("PATCH", `/api/projects/${enc(projectId)}/backlog/${enc(itemId)}`, patch),
  runs: (projectId: string) =>
    send<{ main: string | null; runs: RunBranchWire[] }>("GET", `/api/projects/${enc(projectId)}/runs-review`),
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

export function useProjectRuns(projectId: string): UseQueryResult<{ main: string | null; runs: RunBranchWire[] }> {
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

export function useDecideRun(
  projectId: string,
): UseMutationResult<unknown, Error, { runId: string; accept: boolean; note: string }> {
  const invalidate = useInvalidate(projectId);
  return useMutation({
    mutationFn: ({ runId, accept, note }) => workApi.decide(projectId, runId, accept, note),
    onSuccess: invalidate,
  });
}
