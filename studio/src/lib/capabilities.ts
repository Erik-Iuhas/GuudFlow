// Types + helpers for per-role capability switches (opencode harness, plan §5.7).
// Kept out of types.ts on purpose; AgentConfig / ProjectConfig are extended here.

import type { AgentConfig, ProjectConfig } from "../types";

/** One role's seven toggles, e.g. {sandbox: "strict", web: "off", ...}. */
export type CapabilityBlock = Record<string, string>;

export type HarnessKind = "legacy" | "opencode";

/** GET /api/harness/capabilities */
export interface CapabilitiesMeta {
  levels: Record<string, string[]>;
  role_types: string[];
  defaults: Record<string, CapabilityBlock>;
  host_dev_warning: string;
  production: boolean;
}

/** POST /api/harness/capabilities/resolve */
export interface ResolveCapabilitiesRequest {
  role: string;
  role_type?: string;
  is_reviewer?: boolean;
  model_id?: string;
  capabilities?: CapabilityBlock;
}

export interface ResolveCapabilitiesResponse {
  role_type: string;
  capabilities: CapabilityBlock;
  vision_available: boolean;
  errors: string[];
  warnings: string[];
}

/** AgentConfig plus the opencode-harness fields read by worker.serialise_project. */
export type HarnessAgentConfig = AgentConfig & {
  role_type?: string;
  capabilities?: CapabilityBlock;
};

export type HarnessProjectConfig = Omit<ProjectConfig, "agents"> & {
  harness?: HarnessKind;
  agents?: HarnessAgentConfig[];
};

/** Display order of the toggles. */
export const TOGGLE_ORDER = [
  "sandbox",
  "web",
  "shell",
  "packages",
  "subagents",
  "browser",
  "vision",
] as const;

export const VISION_DISABLED_TITLE = "Model has no image input";
