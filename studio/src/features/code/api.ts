import { getJson } from "../report/api";

// The Code tab's read-only endpoints (api/routes/code.py, plan §15).

export interface CodeEntry {
  path: string;
  /** "" unchanged, "A" added, "M" modified, "D" deleted (against the source's base). */
  status: "" | "A" | "M" | "D";
  added: number;
  removed: number;
}

export interface CodeTree {
  source: "project" | "run" | "role";
  entries: CodeEntry[];
  base: string | null;
  truncated: boolean;
  /** Role source only: the changed file written last. */
  last_edit?: { path: string; at: number } | null;
}

export interface CodeFile {
  path: string;
  text: string | null;
  size: number;
  binary: boolean;
  too_large: boolean;
  deleted: boolean;
  diff: string | null;
}

/** Whose code: the accepted project, this session's main, or one worker's checkout. */
export type CodeSource = { kind: "project" } | { kind: "run" } | { kind: "role"; role: string };

const enc = encodeURIComponent;

function base(projectId: string | undefined, runId: string | undefined, src: CodeSource): string {
  if (src.kind === "project") {
    if (!projectId) throw new Error("no project");
    return `/api/projects/${enc(projectId)}/code`;
  }
  if (!runId) throw new Error("no session");
  return `/api/runs/${enc(runId)}/code`;
}

function roleQuery(src: CodeSource): string {
  return src.kind === "role" ? `role=${enc(src.role)}` : "";
}

export function fetchCodeTree(projectId: string | undefined, runId: string | undefined,
                              src: CodeSource): Promise<CodeTree> {
  const q = roleQuery(src);
  return getJson(`${base(projectId, runId, src)}/tree${q ? `?${q}` : ""}`);
}

export function fetchCodeFile(projectId: string | undefined, runId: string | undefined,
                              src: CodeSource, path: string, diff: boolean): Promise<CodeFile> {
  const q = [`path=${enc(path)}`, roleQuery(src), diff ? "diff=1" : ""].filter(Boolean).join("&");
  return getJson(`${base(projectId, runId, src)}/file?${q}`);
}

export function fetchCodeRoles(runId: string): Promise<{ roles: string[] }> {
  return getJson(`/api/runs/${enc(runId)}/code/roles`);
}

/** Ask the run's Code tab to show a file, or (no path) a worker's checkout. */
export interface OpenCodeDetail {
  path?: string;
  role?: string;
}

export const OPEN_CODE_EVENT = "guudflow:open-code";

export function openInCode(detail: OpenCodeDetail): void {
  window.dispatchEvent(new CustomEvent<OpenCodeDetail>(OPEN_CODE_EVENT, { detail }));
}

/** One shared dependency and where it came from (plan §16). */
export interface DepPackage {
  name: string;
  version: string;
  added_by: string;
  approved_by: string;
  run_id: string;
  at: number;
}

export interface DepsEnvironment {
  manager: "npm" | "pip";
  key: string;
  packages: DepPackage[];
}

export function fetchProjectDeps(projectId: string): Promise<{ environments: DepsEnvironment[] }> {
  return getJson(`/api/projects/${enc(projectId)}/deps`);
}
