import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { getToken } from "../../api";

// The design, the knowledge base, project kinds and phases, and role contracts
// (docs/ROLE_CONTRACTS_PLAN.md; routes in api/routes/design.py and contracts.py).

export interface Origin {
  by?: string;
  draft?: string | null;
  at?: number;
  why?: { name?: string; summary?: string; because?: string } | null;
}

interface Row {
  id: string;
  status: string;
  version?: number;
  origin?: Origin;
}

export interface ComponentWire extends Row {
  name: string;
  paths: string[];
  responsibility: string;
  serves?: string[];
}

export interface InterfaceWire extends Row {
  name: string;
  provider: string;
  kind: string;
  signature: string;
  description?: string;
  users?: string[];
}

export interface DatatypeWire extends Row {
  name: string;
  description?: string;
  fields: { name: string; type: string; meaning?: string }[];
}

export interface FlowWire extends Row {
  title: string;
  kind: string;
  diagram: string;
}

export interface TechDecisionWire extends Row {
  title: string;
  choice: string;
  reason: string;
  binding?: boolean;
  components?: string[];
}

export interface DebtWire extends Row {
  text: string;
  paths?: string[];
  components?: string[];
  how?: string;
}

export interface DesignWire {
  components: ComponentWire[];
  interfaces: InterfaceWire[];
  datatypes: DatatypeWire[];
  flows: FlowWire[];
  decisions: TechDecisionWire[];
  debt: DebtWire[];
  empty: boolean;
}

export interface DesignReasonWire {
  name: string;
  summary: string;
  because: string;
  evidence?: string[];
}

export interface DesignDraftWire {
  id: string;
  status: "open" | "approved" | "discarded";
  by: string;
  run_id?: string | null;
  turn: { groups: DesignReasonWire[]; changes: ({ op: string; group: string } & Record<string, unknown>)[] };
  approved_by?: string;
  discarded_by?: string;
  created_at?: number;
  rejected_groups?: string[];
}

export interface DesignResponse {
  design: DesignWire;
  drafts: DesignDraftWire[];
  markdown: string;
}

export interface FactsModule {
  path: string;
  lines: number;
  exports: string[];
  component?: string | null;
}

export interface FactsWire {
  generated_at: number;
  commit: string;
  files: number;
  modules: FactsModule[];
  tests: string[];
  test_map: Record<string, string[]>;
  commands: string[];
}

export type NoteStatus = "proposed" | "approved" | "stale" | "dropped";

export interface KnowledgeNoteWire {
  id: string;
  text: string;
  topic: string;
  paths: string[];
  status: NoteStatus;
  by: string;
  approved_by?: string;
  stale_since?: string;
  replaces?: string;
}

export interface KnowledgeResponse {
  facts: FactsWire | null;
  notes: KnowledgeNoteWire[];
}

export type PhaseState = "done" | "current" | "todo" | "skipped";

export interface PhaseWire {
  id: string;
  title: string;
  owner: string;
  gate: string;
  state: PhaseState;
  note: string;
}

export interface PhasesResponse {
  kind: string;
  kind_title: string;
  current: string | null;
  phases: PhaseWire[];
  /** Backlog item id → the phase it is in. */
  items: Record<string, string>;
}

export interface KindWire {
  id: string;
  title: string;
  purpose: string;
  start_modes: string[];
  default_start_mode: string;
  lead_contract: string | null;
  team_template: { role: string; role_type: string; contract: string }[];
}

export interface ContractWire {
  id: string;
  title: string;
  purpose: string;
  role_type: string;
  owns: string[];
  produces: string[];
  hands_off: { to: string; what: string }[];
  done_when: string[];
  checked_by: string;
  may_not: string[];
  rules: string[];
  measures: { key: string; description: string }[];
  source: "library" | "project";
}

export interface ProjectContracts {
  contracts: ContractWire[];
  team: { role: string; name?: string | null; role_type: string; contract: string | null }[];
}

export class DesignApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const enc = encodeURIComponent;

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.body ? { "Content-Type": "application/json" } : {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText || `HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new DesignApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const designApi = {
  design: (pid: string) => call<DesignResponse>(`/api/projects/${enc(pid)}/design`),
  approveDraft: (pid: string, draftId: string, rejectGroups: string[] = []) =>
    call<unknown>(`/api/projects/${enc(pid)}/design/drafts/${enc(draftId)}/approve`, {
      method: "POST", body: JSON.stringify({ reject_groups: rejectGroups }),
    }),
  discardDraft: (pid: string, draftId: string) =>
    call<unknown>(`/api/projects/${enc(pid)}/design/drafts/${enc(draftId)}/discard`, { method: "POST" }),
  knowledge: (pid: string) => call<KnowledgeResponse>(`/api/projects/${enc(pid)}/knowledge`),
  rebuildFacts: (pid: string) =>
    call<{ facts: FactsWire }>(`/api/projects/${enc(pid)}/knowledge/rebuild`, { method: "POST" }),
  decideNote: (pid: string, noteId: string, approve: boolean) =>
    call<KnowledgeNoteWire>(
      `/api/projects/${enc(pid)}/knowledge/${enc(noteId)}/${approve ? "approve" : "drop"}`,
      { method: "POST" }),
  phases: (pid: string) => call<PhasesResponse>(`/api/projects/${enc(pid)}/phases`),
  kinds: () => call<{ kinds: KindWire[] }>("/api/kinds"),
  contracts: (pid: string) => call<ProjectContracts>(`/api/projects/${enc(pid)}/contracts`),
  library: () => call<{ contracts: ContractWire[] }>("/api/contracts"),
};

export const designKeys = {
  design: (pid: string) => ["design", pid] as const,
  knowledge: (pid: string) => ["knowledge", pid] as const,
  phases: (pid: string) => ["phases", pid] as const,
  kinds: ["kinds"] as const,
  contracts: (pid: string) => ["contracts", pid] as const,
};

export function useDesign(pid: string): UseQueryResult<DesignResponse, Error> {
  return useQuery({ queryKey: designKeys.design(pid), queryFn: () => designApi.design(pid) });
}

export function useKnowledge(pid: string): UseQueryResult<KnowledgeResponse, Error> {
  return useQuery({ queryKey: designKeys.knowledge(pid), queryFn: () => designApi.knowledge(pid) });
}

export function usePhases(pid: string): UseQueryResult<PhasesResponse, Error> {
  return useQuery({ queryKey: designKeys.phases(pid), queryFn: () => designApi.phases(pid) });
}

export function useKinds(): UseQueryResult<{ kinds: KindWire[] }, Error> {
  return useQuery({ queryKey: designKeys.kinds, queryFn: () => designApi.kinds(), staleTime: 300_000 });
}

export function useContractLibrary(): UseQueryResult<{ contracts: ContractWire[] }, Error> {
  return useQuery({ queryKey: ["contract-library"], queryFn: () => designApi.library(), staleTime: 300_000 });
}

export function useProjectContracts(pid: string): UseQueryResult<ProjectContracts, Error> {
  return useQuery({ queryKey: designKeys.contracts(pid), queryFn: () => designApi.contracts(pid) });
}

export function useDecideDesignDraft(
  pid: string,
): UseMutationResult<unknown, Error, { draftId: string; approve: boolean; rejectGroups?: string[] }> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ draftId, approve, rejectGroups }) =>
      approve ? designApi.approveDraft(pid, draftId, rejectGroups) : designApi.discardDraft(pid, draftId),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: designKeys.design(pid) });
      void client.invalidateQueries({ queryKey: designKeys.phases(pid) });
    },
  });
}

export function useDecideNote(
  pid: string,
): UseMutationResult<KnowledgeNoteWire, Error, { noteId: string; approve: boolean }> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ noteId, approve }) => designApi.decideNote(pid, noteId, approve),
    onSuccess: () => void client.invalidateQueries({ queryKey: designKeys.knowledge(pid) }),
  });
}

export function useRebuildFacts(pid: string): UseMutationResult<{ facts: FactsWire }, Error, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => designApi.rebuildFacts(pid),
    onSuccess: () => void client.invalidateQueries({ queryKey: designKeys.knowledge(pid) }),
  });
}
