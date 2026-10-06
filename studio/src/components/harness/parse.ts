// Defensive parsers for the JSON `detail` payloads the opencode harness posts
// (docs/OPENCODE_HARNESS_PLAN.md §2.1a). Every parser returns null on bad
// input so callers can fall back to the plain summary.

import type { Signal } from "../../types";

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function parseJson(detail: string | null | undefined): unknown {
  if (!detail) return null;
  try {
    return JSON.parse(detail);
  } catch {
    return null;
  }
}

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

/** Render an arbitrary JSON value as display text. */
export function asText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

// ── TOOL_CALL ──────────────────────────────────────────────────────────────

export interface ToolCallInfo {
  tool: string;
  ok: boolean;
  target: string | null;
  durationMs: number | null;
  input: string;
  output: string;
  error: string;
}

const TARGET_KEYS = ["filePath", "file_path", "path", "command", "pattern", "url", "query", "description"];

function targetOf(input: unknown): string | null {
  if (!isRecord(input)) return null;
  for (const k of TARGET_KEYS) {
    const v = str(input[k]);
    if (v) return v;
  }
  return null;
}

export function parseToolCall(detail: string | null | undefined): ToolCallInfo | null {
  const d = parseJson(detail);
  if (!isRecord(d)) return null;
  const tool = str(d.tool);
  if (!tool) return null;
  const status = str(d.status);
  const error = asText(d.error);
  return {
    tool,
    ok: status !== "error" && error === "",
    target: targetOf(d.input),
    durationMs: num(d.duration_ms),
    input: asText(d.input),
    output: asText(d.output),
    error,
  };
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// ── AGENT_RESULT ───────────────────────────────────────────────────────────

export interface ReviewIssue {
  file: string;
  detail: string;
}

export interface AgentResultInfo {
  kind: "agent" | "reviewer";
  status: string | null;
  summary: string | null;
  verdict: "pass" | "fail" | null;
  issues: ReviewIssue[];
  files: string[];
  cost: number | null;
  tokens: number | null;
  toolCalls: number | null;
  commits: number | null;
}

/** `tokens` may be a number or a {input, output, ...} breakdown. */
function totalTokens(v: unknown): number | null {
  const n = num(v);
  if (n !== null) return n;
  if (isRecord(v)) {
    const nums = Object.values(v).map(num).filter((x): x is number => x !== null);
    if (nums.length) return nums.reduce((a, b) => a + b, 0);
  }
  return null;
}

function count(v: unknown): number | null {
  if (Array.isArray(v)) return v.length;
  return num(v);
}

export function parseAgentResult(detail: string | null | undefined): AgentResultInfo | null {
  const d = parseJson(detail);
  if (!isRecord(d) || !isRecord(d.result)) return null;
  const r = d.result;
  const verdictRaw = str(r.verdict);
  const verdict = verdictRaw === "pass" || verdictRaw === "fail" ? verdictRaw : null;
  const issues: ReviewIssue[] = Array.isArray(r.issues)
    ? r.issues.filter(isRecord).map((i) => ({ file: str(i.file) ?? "", detail: str(i.detail) ?? "" }))
    : [];
  const files = strList(d.files);
  return {
    kind: verdict !== null ? "reviewer" : "agent",
    status: str(r.status),
    summary: str(r.summary),
    verdict,
    issues,
    files: files.length ? files : strList(r.files_changed),
    cost: num(d.cost),
    tokens: totalTokens(d.tokens),
    toolCalls: count(d.tool_calls),
    commits: count(d.commits),
  };
}

// ── AGENT_TODO ─────────────────────────────────────────────────────────────

export interface TodoItem {
  content: string;
  status: string;
  priority: string | null;
}

export function parseTodos(detail: string | null | undefined): TodoItem[] | null {
  const d = parseJson(detail);
  if (!Array.isArray(d)) return null;
  return d.filter(isRecord).map((t) => ({
    content: str(t.content) ?? "",
    status: str(t.status) ?? "pending",
    priority: str(t.priority),
  }));
}

// ── CAPABILITIES ───────────────────────────────────────────────────────────

export interface CapabilitiesInfo {
  roleType: string | null;
  effective: Record<string, string>;
  digest: string | null;
  mcpTools: string[];
  model: string | null;
  warnings: string[];
}

export const CAPABILITY_ORDER = ["sandbox", "web", "shell", "packages", "subagents", "browser", "vision"];

export function parseCapabilities(detail: string | null | undefined): CapabilitiesInfo | null {
  const d = parseJson(detail);
  if (!isRecord(d)) return null;
  const eff = isRecord(d.effective) ? d.effective : {};
  const effective: Record<string, string> = {};
  for (const [k, v] of Object.entries(eff)) effective[k] = asText(v);
  return {
    roleType: str(d.role_type),
    effective,
    digest: str(d.digest),
    mcpTools: strList(d.mcp_tools),
    model: str(d.model),
    warnings: strList(d.warnings),
  };
}

/** Warnings from the most recent CAPABILITIES signal for `role`. */
export function latestCapabilityWarnings(signals: Signal[], role: string): string[] {
  for (let i = signals.length - 1; i >= 0; i--) {
    const s = signals[i];
    if (s.type === "CAPABILITIES" && s.from_agent === role) {
      return parseCapabilities(s.detail)?.warnings ?? [];
    }
  }
  return [];
}

// ── tool_permission approvals ──────────────────────────────────────────────

export interface ToolPermissionContext {
  role: string | null;
  permission: string | null;
  patterns: string[];
}

/** Read role / permission / patterns from an APPROVAL_NEEDED detail. Accepts
 *  either JSON (`{..., "context": {role, permission, patterns}}`, or the
 *  fields at top level) or the `key=value; key=value` form the runner uses
 *  (`patterns` separated by `,` or `|`). */
export function parseToolPermissionContext(detail: string | null | undefined): ToolPermissionContext {
  const empty: ToolPermissionContext = { role: null, permission: null, patterns: [] };
  if (!detail) return empty;
  // Backend format (core/harness/approvals.py): "approval_id=..; kind=..; context=<json>".
  // The JSON is last because command patterns may contain ";" or ",".
  const marker = detail.indexOf("context=");
  const json = marker >= 0 ? parseJson(detail.slice(marker + "context=".length)) : parseJson(detail);
  if (isRecord(json)) {
    const c = isRecord(json.context) ? json.context : json;
    const p = c.patterns;
    return {
      role: str(c.role),
      permission: str(c.permission),
      patterns: Array.isArray(p) ? strList(p) : str(p) ? [String(p)] : [],
    };
  }
  const fields: Record<string, string> = {};
  for (const part of detail.split(";")) {
    const i = part.indexOf("=");
    if (i > 0) fields[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return {
    role: fields.role || null,
    permission: fields.permission || null,
    patterns: (fields.patterns ?? "").split(/[,|]/).map((s) => s.trim()).filter(Boolean),
  };
}
