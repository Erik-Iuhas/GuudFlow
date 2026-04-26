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

export const projectsApi = {
  list: () => request<ProjectConfig[]>("GET", "/api/projects"),
  get: (id: string) => request<ProjectConfig>("GET", `/api/projects/${id}`),
  create: (project: ProjectConfig) =>
    request<ProjectConfig>("POST", "/api/projects", project, true),
  update: (id: string, project: ProjectConfig) =>
    request<ProjectConfig>("PUT", `/api/projects/${id}`, project, true),
  remove: (id: string) =>
    request<void>("DELETE", `/api/projects/${id}`, undefined, true),
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
};

// ── Personas ───────────────────────────────────────────────────────────────

export const personasApi = {
  list: () => request<PersonaSummary[]>("GET", "/api/personas"),
  get: (id: string) =>
    request<PersonaSummary>("GET", `/api/personas/${id}`),
  episodes: (id: string, limit = 50) =>
    request<unknown[]>("GET", `/api/personas/${id}/episodes?limit=${limit}`),
  patterns: (id: string) =>
    request<{ patterns: unknown[]; anti_patterns: unknown[] }>(
      "GET",
      `/api/personas/${id}/patterns`,
    ),
};

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
    options?: { projectId?: string; priorRunId?: string },
  ) =>
    request<MetaProposal>(
      "POST",
      "/api/meta/propose",
      {
        goal,
        ...(options?.projectId ? { project_id: options.projectId } : {}),
        ...(options?.priorRunId ? { prior_run_id: options.priorRunId } : {}),
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
  persona: string;
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

export { ApiError };
