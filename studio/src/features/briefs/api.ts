import { getJson } from "../report/api";

// What each worker was told (api/routes/code.py, worker context plan A).

export interface BriefEntry {
  n: number;
  card: string;
  at: number;
  size: number;
}

const enc = encodeURIComponent;

export function fetchBriefs(runId: string): Promise<{ roles: Record<string, BriefEntry[]> }> {
  return getJson(`/api/runs/${enc(runId)}/briefs`);
}

export function fetchBrief(runId: string, role: string, n: number): Promise<{ text: string }> {
  return getJson(`/api/runs/${enc(runId)}/roles/${enc(role)}/briefs/${n}`);
}
