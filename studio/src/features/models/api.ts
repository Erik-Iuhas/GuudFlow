import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { getToken } from "../../api";

// Settings → Models & providers (api/routes/model_config.py). Keys are write-only:
// the server only ever says where a provider's key comes from.

export type ClientKind = "anthropic" | "openai_compatible";
export type KeyStatus = "env" | "stored" | "local" | "missing";
export type Tier = "heavy" | "solid" | "fast";
export const TIERS: Tier[] = ["heavy", "solid", "fast"];

export interface ProviderWire {
  name: string;
  client: ClientKind;
  base_url: string;
  api_key_env: string;
  key: KeyStatus;
}

/** GuudFlow's effort levels, least to most thinking (core/llm/registry.py EFFORT_LEVELS). */
export const EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"] as const;
export type EffortLevel = (typeof EFFORT_LEVELS)[number];

export interface ModelWire {
  id: string;
  provider: string;
  tier: Tier;
  supports_tools?: boolean;
  supports_images?: boolean;
  max_context_tokens?: number;
  max_output_tokens?: number;
  /** USD per 1k tokens (the page shows per million). */
  cost_per_1k_input?: number;
  cost_per_1k_output?: number;
  cache_read_per_1k?: number;
  /** Absent: 1.25x the input price. */
  cache_write_per_1k?: number;
  deprecated?: boolean;
  replacement?: string | null;
  /** The context agents work in (opencode compacts near it); absent: 200k, capped at the max. */
  working_context_tokens?: number;
  /** Prompts above this many tokens are billed at the long-context prices. */
  long_context_threshold?: number;
  long_input_per_1k?: number;
  long_output_per_1k?: number;
  /** Effort levels the model takes (absent: no effort control) and the one agents use by default. */
  effort_levels?: EffortLevel[];
  default_effort?: EffortLevel;
}

export interface ModelConfigView {
  custom: boolean;
  providers: ProviderWire[];
  models: ModelWire[];
  /** tier -> its models; several spread the tier's roles across them (model diversity). */
  tiers: Partial<Record<Tier, string[]>>;
}

export interface ConfigDoc {
  providers: Record<string, { client: ClientKind; base_url?: string; api_key_env: string }>;
  models: ModelWire[];
  tiers: Partial<Record<Tier, string[]>>;
}

export interface TestResult {
  ok: boolean;
  detail: string;
  models: string[];
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

const BASE = "/api/model-config";
const enc = encodeURIComponent;
export const modelConfigKey = ["model-config"] as const;

export const modelConfigApi = {
  get: () => send<ModelConfigView>("GET", BASE),
  save: (doc: ConfigDoc) => send<ModelConfigView>("PUT", BASE, doc),
  reset: () => send<ModelConfigView>("POST", `${BASE}/reset`),
  setKey: (provider: string, key: string) =>
    send<ModelConfigView>("PUT", `${BASE}/providers/${enc(provider)}/key`, { key }),
  clearKey: (provider: string) => send<ModelConfigView>("DELETE", `${BASE}/providers/${enc(provider)}/key`),
  test: (provider: string) => send<TestResult>("POST", `${BASE}/providers/${enc(provider)}/test`),
};

export function useModelConfig(): UseQueryResult<ModelConfigView> {
  return useQuery({ queryKey: modelConfigKey, queryFn: modelConfigApi.get });
}

/** A mutation whose result is the new view: it replaces the cached config. */
export function useConfigMutation<A>(fn: (arg: A) => Promise<ModelConfigView>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (view) => {
      client.setQueryData(modelConfigKey, view);
      void client.invalidateQueries({ queryKey: ["models"] });
    },
  });
}
