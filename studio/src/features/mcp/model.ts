import type { AgentConfig } from "../../types";
import type { McpServerBody, McpServerWire } from "./api";

/** Role types with a default-grants column (the backend knows a few more; they get none by default here). */
export const GRANT_ROLES = [
  ["planner", "Planner"],
  ["builder", "Builder"],
  ["reviewer", "Reviewer"],
  ["researcher", "Researcher"],
  ["tester", "Tester"],
] as const;

const ROLE_TYPES = [
  "researcher", "planner", "architect", "builder", "frontend_builder",
  "tester", "reviewer", "it_agent", "overseer",
];
const ROLE_HINTS: [string, string][] = [
  ["review", "reviewer"], ["research", "researcher"], ["overseer", "overseer"],
  ["tech_lead", "overseer"], ["it_agent", "it_agent"], ["plan", "planner"],
  ["architect", "architect"], ["test", "tester"], ["qa", "tester"],
  ["front", "frontend_builder"], ["ui", "frontend_builder"],
];

function hintMatches(hint: string, name: string, words: string[]): boolean {
  if (hint.includes("_")) return name.includes(hint);
  if (hint.length <= 2) return words.includes(hint);
  return words.some((w) => w.startsWith(hint));
}

/** Mirror of core/harness/capabilities.py infer_role_type, so defaults match the backend. */
export function inferRoleType(agent: Pick<AgentConfig, "role" | "agent_class_role_type" | "is_reviewer">): string {
  const declared = agent.agent_class_role_type ?? "";
  if (ROLE_TYPES.includes(declared)) return declared;
  if (agent.is_reviewer) return "reviewer";
  const name = agent.role.toLowerCase();
  if (ROLE_TYPES.includes(name)) return name;
  const words = name.split(/[^a-z0-9]+/).filter(Boolean);
  return ROLE_HINTS.find(([h]) => hintMatches(h, name, words))?.[1] ?? "builder";
}

export const toolName = (server: string, tool: string): string => `${server}__${tool}`;

/** A tool an enabled server offers, namespaced, with the role types that get it by default. */
export interface OfferedTool {
  name: string;
  server: string;
  tool: string;
  description: string;
  roles: string[];
}

export function offeredTools(servers: McpServerWire[]): OfferedTool[] {
  return servers.filter((s) => s.enabled).flatMap((s) =>
    s.tools.map((t) => ({
      name: toolName(s.name, t.name), server: s.name, tool: t.name,
      description: t.description, roles: s.default_grants[t.name] ?? [],
    })));
}

/** Tools a role gets: null means the registry's defaults for its role type; a list is explicit. */
export function effectiveTools(requested: string[] | null | undefined, roleType: string,
  offers: OfferedTool[]): string[] {
  if (requested == null) return offers.filter((o) => o.roles.includes(roleType)).map((o) => o.name);
  const known = new Set(offers.map((o) => o.name));
  return requested.filter((n) => known.has(n));
}

export type ChipStatus = "connected" | "needs-key" | "off" | "error" | "untested";

export function serverStatus(s: McpServerWire, error?: string): { kind: ChipStatus; label: string } {
  if (!s.enabled) return { kind: "off", label: "off" };
  if (s.key === "missing") return { kind: "needs-key", label: "needs a key" };
  if (error) return { kind: "error", label: "error" };
  if (s.tools.length > 0) {
    return { kind: "connected", label: `connected · ${s.tools.length} tool${s.tools.length === 1 ? "" : "s"}` };
  }
  return { kind: "untested", label: "not tested" };
}

export function targetLabel(s: McpServerWire): string {
  return s.transport === "http" ? `remote · ${s.url}` : `local · ${[s.command, ...s.args].join(" ").trim()}`;
}

export function keyLabel(s: McpServerWire): string {
  switch (s.key) {
    case "env": return `key from $${s.credential.env ?? ""}`;
    case "stored": return "key saved on this machine";
    case "missing": return "key missing";
    default: return "no key";
  }
}

export function toBody(s: McpServerWire | McpServerBody): McpServerBody {
  return {
    name: s.name, transport: s.transport, command: s.command, args: s.args, env: s.env,
    url: s.url, headers: s.headers, credential: s.credential, enabled: s.enabled,
    default_grants: s.default_grants,
  };
}

/** Split an args field the way a shell would for the simple cases (double/single quotes). */
export function splitArgs(text: string): string[] {
  return [...text.matchAll(/"([^"]*)"|'([^']*)'|(\S+)/g)].map((m) => m[1] ?? m[2] ?? m[3] ?? "");
}

export function joinArgs(args: string[]): string {
  return args.map((a) => (/\s/.test(a) ? JSON.stringify(a) : a)).join(" ");
}

/** Add or remove a role type from a tool's default grants (empty lists are dropped). */
export function toggleGrant(grants: Record<string, string[]>, tool: string, role: string,
  on: boolean): Record<string, string[]> {
  const current = grants[tool] ?? [];
  const next = on ? [...new Set([...current, role])] : current.filter((r) => r !== role);
  const { [tool]: _old, ...rest } = grants;
  return next.length ? { ...rest, [tool]: next } : rest;
}
