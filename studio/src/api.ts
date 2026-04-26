// Thin API client. Handles auth + JSON shape so views can stay focused.

import type {
  InvokeResponse,
  MetaProposal,
  PersonaSummary,
  ProjectConfig,
  RunMeta,
  Signal,
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

// ── Meta + Invoke ──────────────────────────────────────────────────────────

export const metaApi = {
  propose: (goal: string) =>
    request<MetaProposal>("POST", "/api/meta/propose", { goal }, true),
};

export const invokeApi = {
  invoke: (projectId: string, payload: unknown = {}) =>
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

export { ApiError };
