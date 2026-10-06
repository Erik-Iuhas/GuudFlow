import type { CapabilitiesMeta, HarnessAgentConfig, RoleCapState } from "../../../lib/capabilities";
import type { PlaybookEntry } from "../../../types";

export const AGENTS: HarnessAgentConfig[] = [
  { role: "planner", description: "Plans the work", depends_on: [], model_tier: "heavy" },
  { role: "builder", description: "Writes the code", depends_on: ["planner"], model_tier: "solid" },
  {
    role: "reviewer", description: "Reviews", depends_on: ["builder"], model_tier: "solid",
    is_reviewer: true, review_of: ["builder"], max_review_rounds: 3,
  },
];

export const LEVELS = {
  sandbox: ["strict", "standard", "host_dev"],
  web: ["off", "search", "search+fetch"],
  shell: ["readonly", "project", "ask"],
  packages: ["off", "via_it_agent"],
  subagents: ["off", "explore_only", "full"],
  browser: ["off", "screenshot"],
  vision: ["off", "on"],
};
export const BUILDER = {
  sandbox: "standard", web: "off", shell: "project", packages: "via_it_agent",
  subagents: "explore_only", browser: "off", vision: "off",
};
export const CAP_META: CapabilitiesMeta = {
  levels: LEVELS, role_types: ["builder"], defaults: { builder: BUILDER, planner: BUILDER, reviewer: BUILDER },
  host_dev_warning: "warn", production: false,
};
export const ROLE_CAPS: Record<string, RoleCapState> = Object.fromEntries(
  AGENTS.map((a) => [a.role, { roleType: a.role, capabilities: { ...BUILDER }, visionAvailable: true, error: null }]),
);

export const ENTRIES: PlaybookEntry[] = [
  { id: "e1", section: "brief_addenda", key: "builder", value: "Read research/notes.md first",
    confidence: 0.8, state: "active", evidence_runs: ["r1", "r2"] },
  { id: "e2", section: "review_rounds", key: "builder>reviewer", value: 3,
    confidence: 0.65, state: "active", evidence_runs: ["r1"] },
];
