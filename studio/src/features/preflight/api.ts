import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { getToken } from "../../api";

// Prepare the session with the tech lead before it starts (docs/WORKER_CONTEXT_PLAN.md H;
// routes in api/routes/preflight.py).

export interface PlannedCardWire {
  id: string;
  title: string;
  owner_role: string;
  acceptance: string[];
  size: "S" | "M" | "L";
  kind: string;
  depends_on?: string[];
  files_hint?: string[];
  backlog_id?: string | null;
  serves?: string[];
  supports?: string[];
  implements?: string[];
  visual_refs?: string[];
}

export interface AssumptionWire {
  id: string;
  text: string;
  decided_by: "tech_lead" | "owner";
  choice: string;
  answer: string;
  decision?: string | null;
}

export interface PreflightMessageWire {
  author: string;
  text: string;
  at: number;
  cost_usd?: number;
}

export interface PreflightWire {
  id: string;
  status: "draft" | "approved";
  items: string[];
  goal: string;
  planner?: string;
  plan: { tasks: PlannedCardWire[]; notes?: string; design?: { changes?: unknown[] } | null } | null;
  assumptions: AssumptionWire[];
  messages: PreflightMessageWire[];
  spend_usd: number;
  problems: string[];
  stale: string[];
  open_questions: string[];
}

export class PreflightApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const enc = encodeURIComponent;

async function call<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const headers: Record<string, string> = body === undefined ? {} : { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new PreflightApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

type Out = { plan: PreflightWire | null };
const base = (pid: string) => `/api/projects/${enc(pid)}/preflight`;

export const preflightApi = {
  get: (pid: string) => call<Out>(base(pid)),
  prepare: (pid: string, items: string[], goal = "") => call<Out>(`${base(pid)}/prepare`, "POST", { items, goal }),
  message: (pid: string, text: string) => call<Out>(`${base(pid)}/messages`, "POST", { text }),
  cards: (pid: string, tasks: PlannedCardWire[]) => call<Out>(`${base(pid)}/cards`, "PUT", { tasks }),
  answer: (pid: string, aid: string, answer: string) =>
    call<Out>(`${base(pid)}/assumptions/${enc(aid)}`, "POST", { answer }),
  approve: (pid: string) => call<Out>(`${base(pid)}/approve`, "POST", {}),
  discard: (pid: string) => call<Out>(`${base(pid)}/discard`, "POST", {}),
};

export const preflightKeys = { plan: (pid: string) => ["preflight", pid] as const };

export function usePreflight(pid: string): UseQueryResult<PreflightWire | null> {
  return useQuery({
    queryKey: preflightKeys.plan(pid),
    queryFn: async () => (await preflightApi.get(pid)).plan,
    enabled: !!pid,
  });
}

/** One preflight action; its answer replaces the cached plan. */
export function usePreflightAction<V>(
  pid: string, fn: (vars: V) => Promise<Out>,
): UseMutationResult<Out, Error, V> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (out) => {
      qc.setQueryData(preflightKeys.plan(pid), out.plan);
      // Answers become spec decisions.
      void qc.invalidateQueries({ queryKey: ["plan"] });
    },
  });
}
