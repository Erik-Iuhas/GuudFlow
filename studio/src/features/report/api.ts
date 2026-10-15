import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getToken } from "../../api";

// Read-only report endpoints (api/routes/run_report.py). Kept local so this folder does not
// touch api.ts, which other Studio work also edits.

export interface CostRow {
  role: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  images: number;
  cost_usd: number;
}

/** Spend on one card from the gateway's labelled rows (plan §11.1, W3). */
export interface CardCostRow {
  card: string;
  roles: string[];
  calls: number;
  cost_usd: number;
  fresh_turns: number;
  kept_turns: number;
  carried_tokens: number;
  cold_calls: number;
  cold_cost_usd: number;
}

export interface CapabilityRow {
  role: string;
  timestamp?: string;
  role_type?: string;
  model?: string;
  requested?: Record<string, string>;
  effective?: Record<string, string>;
  warnings?: string[];
}

export interface WebRow {
  role: string | null;
  timestamp?: string;
  tool: string;
  query?: string;
  url?: string;
  final_url?: string | null;
  status?: number | string | null;
  cache_hit?: boolean;
  bytes?: number;
  refused?: string | null;
}

export interface ReportIntervention {
  signal_id: string;
  timestamp?: string;
  type: string | null;
  target: string;
  rationale: string;
  needs_human: boolean;
  detail: Record<string, unknown>;
  /** null while the run has no retrospective yet. */
  worked: boolean | null;
}

export interface BranchCommit {
  sha: string;
  short_sha: string;
  subject: string;
  author: string;
  committed_at: number;
}

export interface BranchRow {
  role: string;
  branch: string;
  commits: BranchCommit[];
}

export interface RetrospectiveCard {
  task_id: string;
  title: string;
  owner: string;
  column: string;
  review_round: number;
}

export interface RetrospectiveData {
  status: string;
  stall_minutes: number;
  recurring_issues: [string, string][];
  review_rounds_used: Record<string, number>;
  cards: RetrospectiveCard[];
  roles_completed?: string[];
  metrics?: Record<string, number>;
}

export interface RunReportData {
  run_id: string;
  meta: { status?: string; project_id?: string; created_at?: string; goal?: string };
  signal_count: number;
  first_signal_at: string | null;
  last_signal_at: string | null;
  retrospective: RetrospectiveData | null;
  carry_forward: string | null;
  costs: { rows: CostRow[]; total: Omit<CostRow, "role">; by_card?: CardCostRow[] };
  capabilities: CapabilityRow[];
  web: WebRow[];
  interventions: ReportIntervention[];
  branches: BranchRow[];
}

export interface RunMetricsPoint {
  run_id: string;
  recorded_at: string;
  metrics: Record<string, number>;
}

export interface PlaybookEntryWire {
  id: string;
  section: string;
  key: string;
  value: unknown;
  evidence_runs: string[];
  confidence: number;
  state: string;
  needs_review?: boolean;
}

export async function getJson<T>(path: string): Promise<T> {
  const token = getToken();
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

/** `GET /api/runs/{id}/report`. Pass `enabled: false` to wait (e.g. while the run is live). */
export function useRunReport(runId: string, enabled = true): UseQueryResult<RunReportData> {
  return useQuery({
    queryKey: ["runs", runId, "report"] as const,
    queryFn: () => getJson<RunReportData>(`/api/runs/${enc(runId)}/report`),
    enabled,
  });
}

/** `GET /api/runs/{id}/costs`: gateway spend per role so far. Polled every `pollMs` while a
 * run is live, because AGENT_RESULT.cost only arrives when a turn ends. */
export function useRunCosts(
  runId: string | undefined, pollMs: number | false,
): UseQueryResult<RunReportData["costs"]> {
  return useQuery({
    queryKey: ["runs", runId ?? "", "costs"] as const,
    queryFn: () => getJson<RunReportData["costs"]>(`/api/runs/${enc(runId as string)}/costs`),
    enabled: Boolean(runId) && pollMs !== false,
    refetchInterval: pollMs,
  });
}

/** `GET /api/projects/{id}/runs-metrics`: the overseer's per-run metrics, oldest first. */
export function useRunsMetrics(projectId: string): UseQueryResult<RunMetricsPoint[]> {
  return useQuery({
    queryKey: ["projects", projectId, "runs-metrics"] as const,
    queryFn: async () =>
      (await getJson<{ runs: RunMetricsPoint[] }>(`/api/projects/${enc(projectId)}/runs-metrics`)).runs,
  });
}

/** `GET /api/projects/{id}/playbook`. */
export function usePlaybook(projectId: string): UseQueryResult<PlaybookEntryWire[]> {
  return useQuery({
    queryKey: ["projects", projectId, "playbook"] as const,
    queryFn: async () =>
      (await getJson<{ entries: PlaybookEntryWire[] }>(`/api/projects/${enc(projectId)}/playbook`)).entries,
  });
}

/** `GET /api/runs/{id}/git/diff?ref=`: unified diff of one commit. */
export function fetchCommitDiff(runId: string, sha: string): Promise<{ sha: string; diff: string }> {
  return getJson(`/api/runs/${enc(runId)}/git/diff?ref=${enc(sha)}`);
}

export interface RoleChangedFile {
  path: string;
  status: "M" | "A" | "D" | "untracked" | string;
  added: number;
  removed: number;
}

/** A role's work in progress: its worktree against its local `main` (last synced at spawn). */
export interface RoleChanges {
  role: string;
  base: string;
  head: string;
  files: RoleChangedFile[];
  diff: string;
  truncated: boolean;
}

/** `GET /api/runs/{id}/roles/{role}/changes` (read-only; nothing is committed). */
/** `now`: the role's unmerged work in progress; `run`: everything it merged since the run began. */
export type ChangesScope = "now" | "run";

export function fetchRoleChanges(runId: string, role: string, scope: ChangesScope = "now"): Promise<RoleChanges> {
  const q = scope === "run" ? "?scope=run" : "";
  return getJson(`/api/runs/${enc(runId)}/roles/${enc(role)}/changes${q}`);
}
