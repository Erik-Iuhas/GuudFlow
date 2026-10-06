import {
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { approvalsApi, getCards, projectsApi, runsApi } from "../api";
import {
  decideToolPermission,
  type ToolPermissionChoice,
} from "../components/harness/toolPermission";
import type { CardWire, ProjectConfig, RunMeta } from "../types";

// REST reads and actions for the run view, as TanStack Query hooks over the existing
// `api.ts` functions. Live state lives in the run-event store, not here.

/** Query keys, kept in one place so websocket handlers can invalidate them. */
export const queryKeys = {
  run: (runId: string) => ["runs", runId] as const,
  cards: (runId: string) => ["runs", runId, "cards"] as const,
};

/** App-wide client. Retries are modest: the websocket is the live source of truth. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 },
    },
  });
}

/** Run status (`GET /api/runs/{id}`). Pass `pollMs` to keep it fresh while a run is active. */
export function useRunMeta(
  runId: string | undefined,
  options: { pollMs?: number | false } = {},
): UseQueryResult<RunMeta> {
  return useQuery({
    queryKey: queryKeys.run(runId ?? ""),
    queryFn: () => runsApi.get(runId as string),
    enabled: Boolean(runId),
    refetchInterval: options.pollMs ?? false,
  });
}

/** A project's config (`GET /api/projects/{id}`): the role list, review edges, budget cap. */
export function useProject(projectId: string | undefined): UseQueryResult<ProjectConfig> {
  return useQuery({
    queryKey: ["projects", projectId ?? ""] as const,
    queryFn: () => projectsApi.get(projectId as string),
    enabled: Boolean(projectId),
  });
}

/** The run's board cards (`GET /api/runs/{id}/cards`). */
export function useRunCards(runId: string | undefined): UseQueryResult<CardWire[]> {
  return useQuery({
    queryKey: queryKeys.cards(runId ?? ""),
    queryFn: () => getCards(runId as string),
    enabled: Boolean(runId),
  });
}

interface DecideVars {
  approvalId: string;
  decision: "grant" | "deny";
  note?: string;
}

/** Grant or deny an approval gate. The matching APPROVAL_* signal arrives on the websocket. */
export function useDecideApproval(
  runId: string,
): UseMutationResult<Awaited<ReturnType<typeof approvalsApi.decide>>, Error, DecideVars> {
  return useMutation({
    mutationFn: ({ approvalId, decision, note }: DecideVars) =>
      approvalsApi.decide(runId, approvalId, decision, note),
  });
}

interface DecideToolVars {
  approvalId: string;
  choice: ToolPermissionChoice;
  note?: string;
}

/** Answer a tool-permission ask with once / always / reject. */
export function useDecideToolPermission(
  runId: string,
): UseMutationResult<"granted" | "denied", Error, DecideToolVars> {
  return useMutation({
    mutationFn: ({ approvalId, choice, note }: DecideToolVars) =>
      decideToolPermission(runId, approvalId, choice, note),
  });
}

/** Invalidate a run's card list, e.g. when a TASK_ASSIGNED signal announces a new card. */
export function useInvalidateCards(runId: string): () => void {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: queryKeys.cards(runId) });
  };
}
