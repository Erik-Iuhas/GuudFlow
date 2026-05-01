// Thin API client. Handles auth + JSON shape so views can stay focused.

import type {
  InputsState,
  InvokeOutcome,
  InvokeResponse,
  MetaProposal,
  PersonaSummary,
  PriorRunAttachment,
  ProjectConfig,
  RunMeta,
  SecretFinding,
  Signal,
  SourceRepoMeta,
} from "./types";

const TOKEN_KEY = "agentflow.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  authed = false,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (authed) {
    const t = getToken();
    if (t) headers["Authorization"] = `Bearer ${t}`;
  }
  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return undefined as T;
  return (await res.json()) as T;
}

// ── Projects ───────────────────────────────────────────────────────────────

export interface RoadmapResponse {
  content: string;
  exists: boolean;
}

export const projectsApi = {
  list: () => request<ProjectConfig[]>("GET", "/api/projects"),
  get: (id: string) => request<ProjectConfig>("GET", `/api/projects/${id}`),
  create: (project: ProjectConfig) =>
    request<ProjectConfig>("POST", "/api/projects", project, true),
  update: (id: string, project: ProjectConfig) =>
    request<ProjectConfig>("PUT", `/api/projects/${id}`, project, true),
  remove: (id: string) =>
    request<void>("DELETE", `/api/projects/${id}`, undefined, true),
  /** Phase K.0 — read the project's roadmap.md. Returns content="" +
   *  exists=false when the file doesn't exist (default state for new
   *  projects). */
  getRoadmap: (id: string) =>
    request<RoadmapResponse>("GET", `/api/projects/${encodeURIComponent(id)}/roadmap`),
  /** Phase K.0 — write the project's roadmap.md. Empty content
   *  removes the file (so the runner's prompt layer is omitted). */
  updateRoadmap: (id: string, content: string) =>
    request<RoadmapResponse>(
      "PUT",
      `/api/projects/${encodeURIComponent(id)}/roadmap`,
      { content },
      true,
    ),
};

// ── Runs ───────────────────────────────────────────────────────────────────

export const runsApi = {
  list: (projectId?: string) =>
    request<RunMeta[]>(
      "GET",
      `/api/runs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
    ),
  get: (id: string) => request<RunMeta>("GET", `/api/runs/${id}`),
  board: (id: string) => request<Signal[]>("GET", `/api/runs/${id}/board`),
  postComment: (
    id: string,
    text: string,
    priority: "normal" | "high" = "normal",
  ) =>
    request<{
      ok: boolean;
      signal_id: string;
      type: string;
      iteration: number;
    }>(
      "POST",
      `/api/runs/${encodeURIComponent(id)}/comment`,
      { text, priority },
      true,
    ),
  /** Phase G-prime.2 — re-enqueue a Job for a failed/paused run. The
   *  worker resumes from persisted board.jsonl + artifacts.json
   *  rather than seeding fresh. Studio's "Resume" button (G.3) calls
   *  this. 400 if run is complete, 404 if unknown, 409 if already
   *  has an active job. */
  resume: (id: string) =>
    request<{
      accepted: boolean;
      run_id: string;
      job_id: string;
      project_id: string;
    }>("POST", `/api/runs/${encodeURIComponent(id)}/resume`, undefined, true),
  /** Soft-cancel an active run: sets workspace.pending_human so the
   *  scheduler stops dispatching new agents. In-flight agents finish
   *  their current iteration naturally. The run can be resumed via
   *  /resume. 404 if the run isn't active. */
  stop: (id: string) =>
    request<{ ok: boolean; run_id: string }>(
      "POST",
      `/api/runs/${encodeURIComponent(id)}/stop`,
      undefined,
      true,
    ),
  /** Phase G.5 — list review-point tags created by the runner at
   *  every APPROVED merge. Used by the Run view's tag panel. */
  tags: (id: string) =>
    request<RunTag[]>("GET", `/api/runs/${encodeURIComponent(id)}/tags`),
  /** M4.7 — per-agent task desks. */
  desks: (id: string) =>
    request<{ desks: Record<string, unknown[]>; tasks_by_id: Record<string, unknown> }>(
      "GET",
      `/api/runs/${encodeURIComponent(id)}/desks`,
    ),
  /** Hot-reload an agent's config for the live run. 404 if run isn't active
   *  (changes still applied to the project by the caller). */
  patchAgentConfig: (runId: string, role: string, partial: Record<string, unknown>) =>
    request<{ ok: boolean }>(
      "PATCH",
      `/api/runs/${encodeURIComponent(runId)}/agent_config/${encodeURIComponent(role)}`,
      partial,
    ),
  /** Track 1: per-iteration token + timing metrics. */
  iterationMetrics: (id: string) =>
    request<IterationMetrics[]>("GET", `/api/runs/${encodeURIComponent(id)}/iteration_metrics`),
  /** Track 1: per-get_context() call composition snapshots. */
  contextLog: (id: string) =>
    request<ContextSnapshot[]>("GET", `/api/runs/${encodeURIComponent(id)}/context_log`),
};

export interface RunTag {
  name: string;
  sha: string;
  short_sha: string;
  message: string;
  commit_message: string;
  tagged_at: number;
}

// ── Track 1: analytics types ──────────────────────────────────────────────

export interface IterationMetrics {
  run_id: string;
  role: string;
  iteration: number;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  llm_calls: number;
  final_message_tokens: number;
  started_at: number;
  ended_at: number;
  elapsed_seconds: number;
  recall_episodes_count: number;
  recall_patterns_count: number;
  repo_map_tokens: number;
  relevant_files_tokens: number;
  git_diff_tokens: number;
  context_tokens_total: number;
  tools_called: string[];
  completed: boolean;
  failed: boolean;
  no_op: boolean;
}

export interface ContextSnapshot {
  run_id: string;
  role: string;
  iteration: number;
  task: string;
  tier: string;
  max_tokens: number;
  used_tokens: number;
  repo_map_tokens: number;
  git_diff_tokens: number;
  relevant_files: [string, number][];
  recent_signals_count: number;
  recalled_episodes: string[];
  recalled_patterns: string[];
  timestamp: number;
}

// ── Personas ───────────────────────────────────────────────────────────────

export interface PersonaChatRequest {
  message: string;
  project_id?: string;
  run_id?: string;
}

export interface PersonaChatResponse {
  reply: string;
  persona_id: string;
  recall: {
    patterns_used: number;
    anti_patterns_used: number;
    episodes_used: number;
  };
}

// ── Agent catalog (Phase F.2) ──────────────────────────────────────────────

export interface ArchetypeStatusRow {
  name: string;
  abstract_base: string;
  description: string;
  when_to_use: string;
  default_model_tier: string;
  default_tools: string[];
  default_rbac_scopes: string[];
  shipped: boolean;
  n_scenarios: number;
}

export interface CatalogStatus {
  archetypes: ArchetypeStatusRow[];
  n_total: number;
  n_shipped: number;
  n_described_only: number;
  n_scenarios: number;
}

export const agentsApi = {
  /** Phase F.2 — shipped vs described archetype status, with
   *  per-archetype scenario coverage. Used by the studio's catalog
   *  view to surface which archetypes have training-camp authoring. */
  catalogStatus: () => request<CatalogStatus>("GET", "/api/agents/catalog/status"),
};


// ── Training Camp (Phase F.1.2) ────────────────────────────────────────────

export interface TrainingScenario {
  scenario_id: string;
  goal: string;
  project_type: string;
  expected_role_types: string[];
  n_auto_checks: number;
  total_auto_weight: number;
  description: string;
}

export interface TrainingRunResponse {
  project_id: string;
  run_id: string;
  job_id: string;
}

export interface TournamentParticipant {
  persona_id: string;
  project_id: string;
  run_id: string;
  job_id: string;
}

export interface TournamentResponse {
  tournament_id: string;
  scenario_id: string;
  participants: TournamentParticipant[];
}

export interface TournamentResult {
  tournament_id: string;
  scenario_id: string;
  complete: boolean;
  leaderboard: Array<{
    persona_id: string;
    project_id: string;
    run_id: string;
    score: number | null;
  }>;
}

export const trainingApi = {
  listScenarios: () =>
    request<TrainingScenario[]>("GET", "/api/training/scenarios"),
  run: (body: { persona_id: string; scenario_id: string }) =>
    request<TrainingRunResponse>("POST", "/api/training/run", body, true),
  tournament: (body: { scenario_id: string; persona_ids: string[] }) =>
    request<TournamentResponse>("POST", "/api/training/tournament", body, true),
  tournamentResult: (tournamentId: string) =>
    request<TournamentResult>(
      "GET",
      `/api/training/tournaments/${encodeURIComponent(tournamentId)}`,
    ),
};

export interface PersonaRankingRow {
  persona_id: string;
  name: string;
  specialty: string;
  average_score: number;
  n_runs: number;
}

export const personasApi = {
  list: () => request<PersonaSummary[]>("GET", "/api/personas"),
  get: (id: string) =>
    request<PersonaSummary>("GET", `/api/personas/${id}`),
  /** Phase F.1.3 — leaderboard query. Optional filters: role_type
   *  (substring match against id + specialty) and scenario id. */
  rankings: (
    options?: { role_type?: string; scenario?: string; limit?: number },
  ) => {
    const qp = new URLSearchParams();
    if (options?.role_type) qp.set("role_type", options.role_type);
    if (options?.scenario) qp.set("scenario", options.scenario);
    if (options?.limit !== undefined) qp.set("limit", String(options.limit));
    const qs = qp.toString();
    return request<PersonaRankingRow[]>(
      "GET",
      `/api/personas/rankings${qs ? `?${qs}` : ""}`,
    );
  },
  episodes: (id: string, limit = 50) =>
    request<unknown[]>("GET", `/api/personas/${id}/episodes?limit=${limit}`),
  patterns: (id: string) =>
    request<{ patterns: unknown[]; anti_patterns: unknown[] }>(
      "GET",
      `/api/personas/${id}/patterns`,
    ),
  /** Phase F.4 — chat with a persona outside any active run. Read-only:
   *  does not mutate persona memory. */
  chat: (id: string, body: PersonaChatRequest) =>
    request<PersonaChatResponse>(
      "POST",
      `/api/personas/${encodeURIComponent(id)}/chat`,
      body,
      true,
    ),
  /** M6.C5 — persona desk file CRUD. */
  listDeskFiles: (id: string) =>
    request<{ files: DeskFileSummary[] }>(
      "GET",
      `/api/personas/${encodeURIComponent(id)}/desk`,
      undefined,
      true,
    ),
  createDeskFile: (id: string, body: DeskFileWrite) =>
    request<DeskFileSummary>(
      "POST",
      `/api/personas/${encodeURIComponent(id)}/desk`,
      body,
      true,
    ),
  deleteDeskFile: (id: string, filename: string) =>
    request<void>(
      "DELETE",
      `/api/personas/${encodeURIComponent(id)}/desk/${encodeURIComponent(filename)}`,
      undefined,
      true,
    ),
};

export interface DeskFileSummary {
  filename: string;
  triggers: string[];
  agents: string[];
  kind: string;
  body_excerpt: string;
  bytes: number;
}

export interface DeskFileWrite {
  filename: string;
  content: string;
  triggers: string[];
  agents: string[];
  kind: string;
}

// ── Inputs (D-prime §33) ───────────────────────────────────────────────────

export interface UploadResponse {
  uploaded: InputUploadEntry[];
}
export interface InputUploadEntry {
  filename: string;
  size: number;
}
export interface SourceRepoImportSuccess {
  source_repo: SourceRepoMeta;
  warnings: SecretFinding[];
}
export interface SecretsConflict {
  status: 409;
  detail: string;
  findings: SecretFinding[];
}
export interface PriorRunAttachResponse {
  prior_run: PriorRunAttachment;
}

async function uploadMultipart(
  path: string,
  files: File[],
): Promise<UploadResponse> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers["Authorization"] = `Bearer ${t}`;
  const res = await fetch(path, { method: "POST", headers, body: fd });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as UploadResponse;
}

export const inputsApi = {
  list: (projectId: string) =>
    request<InputsState>("GET", `/api/projects/${encodeURIComponent(projectId)}/inputs`),
  uploadFiles: (projectId: string, files: File[]) =>
    uploadMultipart(`/api/projects/${encodeURIComponent(projectId)}/inputs/upload`, files),
  deleteUpload: (projectId: string, filename: string) =>
    request<void>(
      "DELETE",
      `/api/projects/${encodeURIComponent(projectId)}/inputs/uploads/${encodeURIComponent(filename)}`,
      undefined,
      true,
    ),
  importGit: (
    projectId: string,
    body: { git_url: string; branch?: string; shallow?: boolean; force?: boolean },
  ) =>
    request<SourceRepoImportSuccess>(
      "POST",
      `/api/projects/${encodeURIComponent(projectId)}/inputs/git`,
      body,
      true,
    ),
  importLocalPath: (
    projectId: string,
    body: { path: string; force?: boolean },
  ) =>
    request<SourceRepoImportSuccess>(
      "POST",
      `/api/projects/${encodeURIComponent(projectId)}/inputs/local_path`,
      body,
      true,
    ),
  attachPriorRun: (projectId: string, runId: string, feedback?: string) =>
    request<PriorRunAttachResponse>(
      "POST",
      `/api/projects/${encodeURIComponent(projectId)}/inputs/from_run/${encodeURIComponent(runId)}`,
      { feedback: feedback ?? "" },
      true,
    ),
  detachPriorRun: (projectId: string, runId: string) =>
    request<void>(
      "DELETE",
      `/api/projects/${encodeURIComponent(projectId)}/inputs/from_run/${encodeURIComponent(runId)}`,
      undefined,
      true,
    ),
};

// ── Meta + Invoke ──────────────────────────────────────────────────────────

export const metaApi = {
  /** Phase D.2: pass `projectId` to drive the LLM-based MetaAgent.
   *  Pass `priorRunId` to trigger the continuation prompt (D.3) — the
   *  response will populate `deltas` rather than just a fresh team. */
  propose: (
    goal: string,
    options?: { projectId?: string; priorRunId?: string; metaModel?: string },
  ) =>
    request<MetaProposal>(
      "POST",
      "/api/meta/propose",
      {
        goal,
        ...(options?.projectId ? { project_id: options.projectId } : {}),
        ...(options?.priorRunId ? { prior_run_id: options.priorRunId } : {}),
        ...(options?.metaModel ? { meta_model: options.metaModel } : {}),
      },
      true,
    ),
};

export const invokeApi = {
  /** First call returns cost-preview when the gate is on; second call
   *  with `confirm: true` actually enqueues. Studio handles both shapes. */
  invoke: (projectId: string, payload: Record<string, unknown> = {}) =>
    request<InvokeOutcome>(
      "POST",
      `/api/invoke/${encodeURIComponent(projectId)}`,
      payload,
      true,
    ),
  /** @deprecated Use `invoke` and check the return type. Kept for any
   *  existing callers; same wire shape but typed as the always-202 case. */
  invokeAndExpectAccepted: (projectId: string, payload: unknown = {}) =>
    request<InvokeResponse>(
      "POST",
      `/api/invoke/${encodeURIComponent(projectId)}`,
      payload,
      true,
    ),
  jobStatus: (jobId: string) =>
    request<{
      status: string;
      started_at?: string | null;
      finished_at?: string | null;
      error?: string | null;
    }>("GET", `/api/invoke/${jobId}/status`, undefined, true),
};

export const approvalsApi = {
  /** Resolve a pending approval gate. Used by ApprovalCard. */
  decide: (
    runId: string,
    approvalId: string,
    decision: "grant" | "deny",
    note?: string,
  ) =>
    request<{
      ok: boolean;
      approval_id: string;
      decision: "granted" | "denied";
      type: "APPROVAL_GRANTED" | "APPROVAL_DENIED";
    }>(
      "POST",
      `/api/runs/${encodeURIComponent(runId)}/approval/${encodeURIComponent(approvalId)}`,
      { decision, note },
      true,
    ),
};

// ── WebSocket helper ───────────────────────────────────────────────────────

export function connectRunStream(
  runId: string,
  onMessage: (msg: { type: string; signals?: Signal[]; signal?: Signal }) => void,
  onClose?: () => void,
): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/ws/runs/${encodeURIComponent(runId)}`;
  const ws = new WebSocket(url);
  ws.addEventListener("message", (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      /* ignore malformed */
    }
  });
  if (onClose) {
    ws.addEventListener("close", onClose);
    ws.addEventListener("error", onClose);
  }
  return ws;
}

export interface QuipWire {
  ts: string;
  persona: string;   // display name
  role?: string;     // agent role (absent in old quip logs)
  text: string;
  event: string;
}

export function connectQuipStream(
  runId: string,
  onMessage: (msg: {
    type: string;
    quips?: QuipWire[];
    quip?: QuipWire;
    live?: boolean;
  }) => void,
  onClose?: () => void,
): WebSocket {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/ws/runs/${encodeURIComponent(runId)}/quips`;
  const ws = new WebSocket(url);
  ws.addEventListener("message", (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      /* ignore malformed */
    }
  });
  if (onClose) {
    ws.addEventListener("close", onClose);
    ws.addEventListener("error", onClose);
  }
  return ws;
}

import type { ToolEntry } from "./components/ToolsMultiSelect";

export interface DashboardData {
  active_runs: {
    run_id: string;
    project_id: string;
    project_name: string;
    goal: string;
    status: string;
    current_agents: { role: string; state: string; persona_id?: string }[];
    started_at?: string;
    elapsed_s: number;
    task_progress: { closed: number; total: number };
  }[];
  recent_completions: {
    run_id: string;
    project_id: string;
    outcome: string;
    duration_s?: number;
    ended_at?: string;
  }[];
  pending_approvals: {
    run_id: string;
    approval_id: string;
    title: string;
    kind: string;
  }[];
  top_personas: {
    persona_id: string;
    name: string;
    role: string;
    recent_runs: number;
    win_rate: number;
  }[];
}

export const dashboardApi = {
  get: () => request<DashboardData>("GET", "/api/dashboard", undefined, true),
};

export const toolsApi = {
  list: () =>
    request<{ tools: ToolEntry[] }>("GET", "/api/tools", undefined, true).then(
      (r) => r.tools,
    ),
};

export interface ModelEntry {
  id: string;
  provider: string;
  tier: "heavy" | "solid" | "fast";
  supports_tools: boolean;
  deprecated: boolean;
}

export const modelsApi = {
  list: () =>
    request<{ models: ModelEntry[] }>("GET", "/api/models").then(
      (r) => r.models.filter((m) => !m.deprecated && m.supports_tools),
    ),
};

export { ApiError };
