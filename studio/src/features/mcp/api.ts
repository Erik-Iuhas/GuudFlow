import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { getToken } from "../../api";

// Settings → MCP servers (api/routes/mcp.py). Keys are write-only: the server only
// says where a server's key comes from.

export type McpTransport = "stdio" | "http";
export type McpKeyStatus = "env" | "stored" | "missing" | "none";

export interface McpToolWire {
  name: string;
  description: string;
}

export interface McpServerWire {
  name: string;
  transport: McpTransport;
  command: string;
  args: string[];
  env: string[];
  url: string;
  headers: Record<string, string>;
  credential: { env?: string };
  enabled: boolean;
  /** tool (un-namespaced) -> role types that get it by default. */
  default_grants: Record<string, string[]>;
  tools: McpToolWire[];
  key: McpKeyStatus;
}

/** What POST / PUT accept (the server's view minus tools and key status). */
export type McpServerBody = Omit<McpServerWire, "tools" | "key">;

export interface McpTestResult {
  ok: boolean;
  tool_count?: number;
  error?: string;
  server: McpServerWire;
}

export interface McpImportResult {
  imported: string[];
  skipped: { name: string; reason: string }[];
  warnings: string[];
  servers: McpServerWire[];
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

const BASE = "/api/mcp/servers";
const enc = encodeURIComponent;
export const mcpKey = ["mcp-servers"] as const;

export const mcpApi = {
  list: async () => (await send<{ servers: McpServerWire[] }>("GET", BASE)).servers,
  add: (body: McpServerBody) => send<McpServerWire>("POST", BASE, body),
  replace: (name: string, body: McpServerBody) => send<McpServerWire>("PUT", `${BASE}/${enc(name)}`, body),
  remove: (name: string) => send<{ deleted: string }>("DELETE", `${BASE}/${enc(name)}`),
  setKey: (name: string, key: string) => send<McpServerWire>("PUT", `${BASE}/${enc(name)}/key`, { key }),
  clearKey: (name: string) => send<McpServerWire>("DELETE", `${BASE}/${enc(name)}/key`),
  test: (name: string) => send<McpTestResult>("POST", `${BASE}/${enc(name)}/test`),
  importJson: (doc: unknown, replace: boolean) =>
    send<McpImportResult>("POST", `${BASE}/import${replace ? "?replace=true" : ""}`, doc),
};

export function useMcpServers(): UseQueryResult<McpServerWire[]> {
  return useQuery({ queryKey: mcpKey, queryFn: mcpApi.list });
}

/** A mutation that refreshes the server list afterwards. */
export function useMcpMutation<A, R>(fn: (arg: A) => Promise<R>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => client.invalidateQueries({ queryKey: mcpKey }),
  });
}
