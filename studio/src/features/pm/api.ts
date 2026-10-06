import { useEffect, useState } from "react";
import { getToken } from "../../api";

// Jack, the project manager persona (docs/PROJECT_FLOW_PLAN.md §10.1). Studio shows his
// profile name wherever it used to say "Project manager".

export const DEFAULT_PM_NAME = "Jack";

export interface PmWire {
  persona_id: string;
  name: string;
  specialty: string;
  episodes: number;
  patterns: number;
  reputation: Record<string, number>;
}

const CACHE_MS = 10 * 60 * 1000;
const RETRY_MS = 60 * 1000;

let cached: { name: string; at: number; ok: boolean } | null = null;
let inflight: Promise<string> | null = null;

/** Forgets the cached name (tests, and after the persona is renamed). */
export function resetPmName(): void {
  cached = null;
  inflight = null;
}

export async function fetchPm(): Promise<PmWire> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch("/api/pm", { headers });
  if (!res.ok) throw new Error(res.statusText || `HTTP ${res.status}`);
  return (await res.json()) as PmWire;
}

function fresh(): boolean {
  return cached !== null && Date.now() - cached.at < (cached.ok ? CACHE_MS : RETRY_MS);
}

function load(): Promise<string> {
  inflight ??= fetchPm()
    .then((pm) => {
      const name = typeof pm.name === "string" && pm.name.trim() ? pm.name.trim() : DEFAULT_PM_NAME;
      cached = { name, at: Date.now(), ok: true };
      return name;
    })
    .catch(() => {
      cached = { name: cached?.name ?? DEFAULT_PM_NAME, at: Date.now(), ok: false };
      return cached.name;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** The project manager's display name: Jack's profile name, "Jack" until it is known. */
export function usePmName(): string {
  const [name, setName] = useState(cached?.name ?? DEFAULT_PM_NAME);
  useEffect(() => {
    if (fresh()) {
      setName(cached!.name);
      return;
    }
    let live = true;
    void load().then((n) => {
      if (live) setName(n);
    });
    return () => {
      live = false;
    };
  }, []);
  return name;
}
