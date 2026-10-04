// Thin API client. Handles auth + JSON shape so views can stay focused.

import type {
  CardWire,
  ExampleProjectSummary,
  InputsState,
  InstantiateExampleProjectResponse,
  InvokeOutcome,
  InvokeResponse,
  MetaProposal,
  OneShotRequestBody,
  OneShotResponse,
  PersonaSummary,
  PlaybookResponse,
  PriorRunAttachment,
  ProjectConfig,
  RunMeta,
  SecretFinding,
  Signal,
  SourceRepoMeta,
} from "./types";
import type {
  CapabilitiesMeta,
  ResolveCapabilitiesRequest,
  ResolveCapabilitiesResponse,
} from "./lib/capabilities";

const TOKEN_KEY = "guudflow.token";

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
  timeoutMs = 30000,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (authed) {
    const t = getToken();
    if (t) headers["Authorization"] = `Bearer ${t}`;
  }
  // 30s default timeout — prevents indefinite hangs on slow endpoints.
  // Settings validation uses a tighter timeout via its own AbortController.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      throw new ApiError(0, `request timed out after ${timeoutMs}ms`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
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
  /** Playbook entries (plan 11.4) and run-metrics history; read-only. */
  playbook: (id: string) =>
    request<PlaybookResponse>(
      "GET",
      `/api/projects/${encodeURIComponent(id)}/playbook`,
      undefined,
      true,
    ),
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

// ── Example projects (one-click templates) ────────────────────────────────

export const exampleProjectsApi = {
  list: () =>
    request<ExampleProjectSummary[]>("GET", "/api/example-projects"),
  /** Create a real project from a template. Pass `project_id` to use an
   *  explicit slug (collisions return 409); otherwise the backend
   *  auto-suffixes the template's default. Pass `goal` to override the
   *  template's default goal. */
  instantiate: (
    templateId: string,
    body: { project_id?: string; goal?: string } = {},
  ) =>
    request<InstantiateExampleProjectResponse>(
      "POST",
      `/api/example-projects/${encodeURIComponent(templateId)}/instantiate`,
      body,
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
  /** Track 11 — live cognition snapshot for the Workers tab. Studio
   *  polls this every ~1.5s while the tab is visible. */
  workers: (id: string) =>
    request<WorkersSnapshot>(
      "GET",
      `/api/runs/${encodeURIComponent(id)}/workers`,
      undefined,
      true,
    ),
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
    request<IterationMetrics[]>("GET", `/api/runs/${encodeURIComponent(id)}/iteration_metrics`, undefined, true),
  /** Track 1: per-get_context() call composition snapshots. */
  contextLog: (id: string) =>
    request<ContextSnapshot[]>("GET", `/api/runs/${encodeURIComponent(id)}/context_log`, undefined, true),
};

// Track 11 — Worker cognition view payload.
export interface WorkerCurrentToolCall {
  name: string;
  arguments_preview: string;
  elapsed_seconds: number;
}
export interface WorkerLastToolSummary {
  name: string;
  summary: string;
  ago_seconds: number;
}
export interface WorkerActiveSubtask {
  id: string;
  goal: string;
  parent_task_id: string | null;
  acceptance: string[];
}
export interface WorkerSubtaskHistoryEntry {
  id: string;
  goal: string;
  status: string;
  parent_task_id?: string | null;
}
export interface WorkerCognition {
  role: string;
  state: string;
  iteration: number;
  active_subtask: WorkerActiveSubtask | null;
  current_tool_call: WorkerCurrentToolCall | null;
  last_tool_summary: WorkerLastToolSummary | null;
  iteration_cost_usd: number;
  subtask_history: WorkerSubtaskHistoryEntry[];
}
export interface WorkersSnapshot {
  workers: WorkerCognition[];
  live: boolean;
}


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
  /** Track 12 — per-use-case sub-LLM dispatch breakdown. Keyed by
   *  use_case (e.g. "summarise-subtask", "protocol-cleanup"); each
   *  entry has {calls, input_tokens, output_tokens}. */
  sub_llm_breakdown?: Record<string, { calls: number; input_tokens: number; output_tokens: number }>;
  /** Track 8 — subtask context hot-swap counters. */
  subtask_messages_stashed?: number;
  subtask_messages_restored?: number;
  /** Track 3d.5 — total estimated USD cost for this iteration row.
   *  Computed runner-side from per-1k pricing in data/models.json
   *  PLUS sub_llm_breakdown contributions. 0.0 when no registry. */
  estimated_cost_usd?: number;
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
  importLocalPathInPlace: (
    projectId: string,
    body: { path: string },
  ) =>
    request<SourceRepoImportSuccess>(
      "POST",
      `/api/projects/${encodeURIComponent(projectId)}/inputs/local_path_in_place`,
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
  detachSourceRepo: (projectId: string) =>
    request<void>(
      "DELETE",
      `/api/projects/${encodeURIComponent(projectId)}/inputs/source_repo`,
      undefined,
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

export const oneshotApi = {
  /** POST /api/oneshot. 202 either enqueues the run or asks for approval. */
  start: (body: OneShotRequestBody) =>
    request<OneShotResponse>("POST", "/api/oneshot", body, true),
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

/** The run's board cards (works for active and finished runs). */
export async function getCards(runId: string): Promise<CardWire[]> {
  const res = await request<{ cards: CardWire[] }>(
    "GET",
    `/api/runs/${encodeURIComponent(runId)}/cards`,
  );
  return res?.cards ?? [];
}

// ── WebSocket helper ───────────────────────────────────────────────────────

/** Auto-reconnecting WebSocket wrapper. Caller treats it as a `{ close }`
 *  handle. Internally we create a fresh WebSocket on drop with exponential
 *  backoff (capped at 5s) until `close()` is called. Survives uvicorn
 *  --reload restarts so the dashboard / Run view never get stuck on
 *  "disconnected" after a backend reload. */
export interface WsHandle {
  close: () => void;
  /** True only while the underlying socket is OPEN. */
  isConnected: () => boolean;
}

function _connectWithReconnect(
  url: string,
  onMessage: (data: unknown) => void,
  onStateChange?: (connected: boolean) => void,
): WsHandle {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoffMs = 500;
  let reconnectTimer: number | null = null;
  // Bug fix: only reset backoff after the connection has been STABLE for
  // a meaningful interval. The previous "reset on open" path created a
  // tight reconnect loop when the server accepted then immediately
  // closed the socket (e.g. the run is no longer in the registry) —
  // hundreds of connect → close cycles in seconds.
  const STABLE_MS = 3000;
  // Hard cap on consecutive failed attempts. Once exceeded, give up
  // entirely instead of pounding the server forever. Caller can re-create
  // the handle by re-rendering the component.
  const MAX_ATTEMPTS = 12;
  let attempts = 0;
  let stableTimer: number | null = null;

  const open = () => {
    if (closed) return;
    if (attempts >= MAX_ATTEMPTS) {
      closed = true;
      onStateChange?.(false);
      return;
    }
    attempts += 1;
    ws = new WebSocket(url);
    ws.addEventListener("open", () => {
      onStateChange?.(true);
      // Don't reset attempts/backoff yet — wait for STABLE_MS of uptime
      // OR a real message. Either proves the server isn't immediately
      // closing on us.
      if (stableTimer !== null) window.clearTimeout(stableTimer);
      stableTimer = window.setTimeout(() => {
        attempts = 0;
        backoffMs = 500;
        stableTimer = null;
      }, STABLE_MS);
    });
    ws.addEventListener("message", (ev) => {
      // First message also counts as "stable enough" — reset immediately.
      attempts = 0;
      backoffMs = 500;
      if (stableTimer !== null) {
        window.clearTimeout(stableTimer);
        stableTimer = null;
      }
      try {
        onMessage(JSON.parse(ev.data));
      } catch {
        /* ignore malformed */
      }
    });
    const handleDrop = () => {
      if (closed) return;
      // Cancel the stability timer — we never reached stable.
      if (stableTimer !== null) {
        window.clearTimeout(stableTimer);
        stableTimer = null;
      }
      onStateChange?.(false);
      // Don't double-schedule if both close + error fire.
      if (reconnectTimer !== null) return;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        backoffMs = Math.min(backoffMs * 2, 5000);
        open();
      }, backoffMs);
    };
    ws.addEventListener("close", handleDrop);
    ws.addEventListener("error", handleDrop);
  };

  open();

  return {
    close: () => {
      closed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (stableTimer !== null) {
        window.clearTimeout(stableTimer);
        stableTimer = null;
      }
      if (ws) {
        try { ws.close(); } catch { /* ignore */ }
        ws = null;
      }
    },
    isConnected: () => ws !== null && ws.readyState === WebSocket.OPEN,
  };
}

export function connectRunStream(
  runId: string,
  onMessage: (msg: { type: string; signals?: Signal[]; signal?: Signal; live?: boolean }) => void,
  onClose?: () => void,
  /** Optional: called with true on open and false on every drop (including the final give-up). */
  onStateChange?: (connected: boolean) => void,
): WsHandle {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/ws/runs/${encodeURIComponent(runId)}`;
  return _connectWithReconnect(
    url,
    (data) =>
      onMessage(data as { type: string; signals?: Signal[]; signal?: Signal; live?: boolean }),
    (connected) => {
      if (!connected) onClose?.();
      onStateChange?.(connected);
    },
  );
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
): WsHandle {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/ws/runs/${encodeURIComponent(runId)}/quips`;
  return _connectWithReconnect(
    url,
    (data) => onMessage(data as { type: string; quips?: QuipWire[]; quip?: QuipWire; live?: boolean }),
    (connected) => { if (!connected) onClose?.(); },
  );
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

export const harnessApi = {
  /** Toggle levels, role types and per-role-type defaults (opencode harness). */
  capabilities: () =>
    request<CapabilitiesMeta>("GET", "/api/harness/capabilities", undefined, true),
  /** Merge overrides onto role defaults; gates vision by the model. 422 on invalid values. */
  resolve: (body: ResolveCapabilitiesRequest) =>
    request<ResolveCapabilitiesResponse>(
      "POST",
      "/api/harness/capabilities/resolve",
      body,
      true,
    ),
};

export interface ModelEntry {
  id: string;
  provider: string;
  tier: "heavy" | "solid" | "fast";
  supports_tools: boolean;
  deprecated: boolean;
  /** Track 12 — surfaced in the sub-LLM model picker so the user
   *  picks honestly. Numbers come straight from data/models.json. */
  cost_per_1k_input?: number;
  cost_per_1k_output?: number;
}

export const modelsApi = {
  list: () =>
    request<{ models: ModelEntry[] }>("GET", "/api/models").then(
      (r) => r.models.filter((m) => !m.deprecated && m.supports_tools),
    ),
};

// ── Settings (Track 4) ─────────────────────────────────────────────────────

export interface SettingsState {
  effective: {
    storage_mode: "global" | "in-directory";
    storage_root: string;
    personas_root: string;
  };
  persisted: Record<string, string>;
  env_overrides_active: boolean;
}

export interface SettingsUpdateResponse {
  persisted: Record<string, string>;
  restart_required: boolean;
  hot_reloaded?: boolean;
}

export interface StorageRootValidation {
  valid: boolean;
  exists: boolean;
  writable: boolean;
  reason: string | null;
}

export const settingsApi = {
  get: () => request<SettingsState>("GET", "/api/settings"),
  update: (updates: Partial<{
    storage_mode: "global" | "in-directory";
    storage_root: string;
    personas_root: string;
    data_root: string;
  }>) =>
    request<SettingsUpdateResponse>("PUT", "/api/settings", updates),
  // Tighter timeout (8s) — path validation hits the filesystem and can
  // hang on slow / unresponsive mounts. Better to fail fast than freeze
  // the drawer.
  validateStorageRoot: (path: string) =>
    request<StorageRootValidation>(
      "POST", "/api/settings/validate-storage-root", { path }, false, 8000,
    ),
};

export { ApiError };
