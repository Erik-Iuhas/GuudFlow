import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getToken } from "../../api";

// The project record: trace, export and the files kept in the repo
// (docs/PROJECT_FLOW_PLAN.md, P8; routes in api/routes/record.py).

export type RecordEventKind = "spec" | "decision" | "plan" | "work" | "session" | "review" | "status";

export interface RecordEventWire {
  at: number;
  kind: RecordEventKind;
  ref: string;
  text: string;
  by: string;
  links: string[];
}

export interface TraceWire {
  ref: string;
  events: RecordEventWire[];
}

export type ExportSection = "spec" | "decisions" | "sessions" | "plan" | "rules" | "costs";
export type ExportFormat = "md" | "json";

/** Sections in the order the server lists them, so the URL is the same for the same ticks. */
export const EXPORT_SECTIONS: readonly ExportSection[] = ["spec", "decisions", "sessions", "plan", "rules", "costs"];

export class RecordApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const enc = encodeURIComponent;

async function get(path: string): Promise<Response> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { method: "GET", headers });
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new RecordApiError(res.status, detail);
  }
  return res;
}

export function exportUrl(projectId: string, include: readonly ExportSection[], format: ExportFormat): string {
  const ordered = EXPORT_SECTIONS.filter((s) => include.includes(s));
  return `/api/projects/${enc(projectId)}/record/export?format=${format}&include=${ordered.join(",")}`;
}

export const recordApi = {
  trace: async (projectId: string, ref: string): Promise<TraceWire> =>
    (await get(`/api/projects/${enc(projectId)}/record?ref=${enc(ref)}`)).json() as Promise<TraceWire>,
  specFiles: async (projectId: string): Promise<{ files: Record<string, string> }> =>
    (await get(`/api/projects/${enc(projectId)}/record/spec-files`)).json() as Promise<{ files: Record<string, string> }>,
  /** Fetches the export with the bearer token (a plain link could not carry it). */
  download: async (
    projectId: string,
    include: readonly ExportSection[],
    format: ExportFormat,
  ): Promise<{ blob: Blob; filename: string }> => {
    const res = await get(exportUrl(projectId, include, format));
    const disposition = res.headers?.get?.("Content-Disposition") ?? "";
    const named = /filename="([^"]+)"/.exec(disposition);
    return { blob: await res.blob(), filename: named?.[1] ?? `${projectId}-record.${format}` };
  },
};

export const recordKeys = {
  trace: (projectId: string, ref: string) => ["record", projectId, "trace", ref] as const,
  specFiles: (projectId: string) => ["record", projectId, "spec-files"] as const,
};

export function useTrace(projectId: string, ref: string): UseQueryResult<TraceWire> {
  return useQuery({
    queryKey: recordKeys.trace(projectId, ref),
    queryFn: () => recordApi.trace(projectId, ref),
    enabled: ref !== "",
    retry: false,
  });
}

export function useSpecFiles(projectId: string): UseQueryResult<{ files: Record<string, string> }> {
  return useQuery({ queryKey: recordKeys.specFiles(projectId), queryFn: () => recordApi.specFiles(projectId) });
}
