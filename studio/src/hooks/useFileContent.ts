import { useEffect, useState } from "react";
import { getToken } from "../api";

export interface FileContent {
  path: string;
  content?: string;
  encoding?: "utf-8" | "base64";
  error?: string;
}

export interface FileHistoryEntry {
  sha: string;
  subject: string;
  author_role: string;
  committed_at: number;
}

export function useFileContent(
  runId: string | null,
  filePath: string | null,
  ref?: string | null,
): { data: FileContent | null; loading: boolean; error: string | null } {
  const [data, setData] = useState<FileContent | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId || !filePath) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    setData(null);
    const token = getToken();
    const qs = new URLSearchParams({ path: filePath });
    if (ref) qs.set("ref", ref);
    fetch(`/api/runs/${encodeURIComponent(runId)}/files?${qs}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<FileContent>;
      })
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [runId, filePath, ref]);

  return { data, loading, error };
}

export function useFileHistory(
  runId: string | null,
  filePath: string | null,
): { history: FileHistoryEntry[]; loading: boolean } {
  const [history, setHistory] = useState<FileHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId || !filePath) {
      setHistory([]);
      return;
    }
    setLoading(true);
    const token = getToken();
    fetch(
      `/api/runs/${encodeURIComponent(runId)}/files/history?path=${encodeURIComponent(filePath)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    )
      .then((r) => (r.ok ? (r.json() as Promise<FileHistoryEntry[]>) : Promise.resolve([])))
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, [runId, filePath]);

  return { history, loading };
}
