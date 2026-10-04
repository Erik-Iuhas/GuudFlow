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
  /** Roles whose work this role reviews (scheduler review edge). */
  review_of?: string[];
  max_review_rounds?: number;
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

/** Plain-language explanation of each toggle, shown as a tooltip on the team screen. */
export const TOGGLE_TOOLTIPS: Record<(typeof TOGGLE_ORDER)[number], string> = {
  sandbox:
    "Where this role's code runs. strict and standard keep it in an isolated container with no internet; host_dev runs it on your machine and is for trusted local demos only.",
  web:
    "Lets this role look things up on the web. It never touches the internet directly: searches and page fetches run through GuudFlow on your side. Off means no web access at all.",
  shell:
    "What commands this role may run. readonly lets it only look around, project lets it run commands inside its own copy of the project, ask pauses for your approval first.",
  packages:
    "Lets this role request libraries to be installed. Pinned packages on the project allow-list install automatically; anything else waits for your approval. Off means it works with what is already there.",
  subagents:
    "Lets this role hand small read-only searches to a cheap helper. explore_only helpers can only read; full helpers can also change files. Off means the role works alone.",
  browser:
    "Lets this role open the running app and take screenshots to check how it looks. Screenshots are taken on your machine, never inside the sandbox.",
  vision:
    "Lets this role look at images such as screenshots and design mockups. Needs a model that accepts images, and costs more per turn.",
};

/** Short chips for a node, e.g. ["web: search", "shell: project"]; defaults are omitted. */
export function capabilitySummary(caps: CapabilityBlock | undefined): string[] {
  if (!caps) return [];
  const quiet = new Set(["off", "readonly", "strict"]);
  const out: string[] = [];
  for (const name of TOGGLE_ORDER) {
    const level = caps[name];
    if (level && !quiet.has(level)) out.push(`${name}: ${level}`);
  }
  return out;
}

/**
 * Rough cost multiplier for a role's capabilities. Web research adds lookups,
 * subagents add helper turns and vision adds image tokens. Estimate only.
 */
export function capabilityCostFactor(caps: CapabilityBlock | undefined): number {
  if (!caps) return 1;
  let factor = 1;
  if (caps.web && caps.web !== "off") factor += caps.web === "search+fetch" ? 0.15 : 0.08;
  if (caps.subagents === "explore_only") factor += 0.05;
  if (caps.subagents === "full") factor += 0.2;
  if (caps.vision && caps.vision !== "off") factor += 0.15;
  if (caps.browser && caps.browser !== "off") factor += 0.05;
  return factor;
}

/** Per-role capability state kept alongside a team proposal (opencode harness). */
export interface RoleCapState {
  roleType: string;
  capabilities: CapabilityBlock;
  visionAvailable: boolean;
  error: string | null;
}
