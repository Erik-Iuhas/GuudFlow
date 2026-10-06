// Tool-permission approvals (plan §5.1, §5.9): an agent asked to use a tool
// that the role's ruleset marks `ask`. Three answers instead of grant/deny.

import { ApiError, getToken } from "../../api";
import { Button } from "../ui/button";
import { parseToolPermissionContext } from "./parse";

export type ToolPermissionChoice = "once" | "run" | "reject";

export interface ToolPermissionResponse {
  decision?: "granted" | "denied";
}

/** POST /api/runs/{run}/approval/{id} with the scope-aware body. Mirrors the
 *  request/auth/error handling of `approvalsApi.decide` (api.ts is owned
 *  elsewhere, and its helper has no `scope` parameter). */
export async function decideToolPermission(
  runId: string,
  approvalId: string,
  choice: ToolPermissionChoice,
  note?: string,
): Promise<"granted" | "denied"> {
  const body: Record<string, unknown> =
    choice === "reject" ? { decision: "deny" } : { decision: "grant", scope: choice };
  if (note) body.note = note;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/approval/${encodeURIComponent(approvalId)}`,
    { method: "POST", headers, body: JSON.stringify(body) },
  );
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  let parsed: ToolPermissionResponse = {};
  try {
    parsed = (await res.json()) as ToolPermissionResponse;
  } catch {
    /* body is optional */
  }
  return parsed.decision ?? (choice === "reject" ? "denied" : "granted");
}

/** Readable role / permission / patterns for a tool-permission approval. */
export function ToolPermissionSummary({ detail }: { detail: string | null | undefined }) {
  const ctx = parseToolPermissionContext(detail);
  if (!ctx.role && !ctx.permission && ctx.patterns.length === 0) return null;
  return (
    <p className="small my-1" data-testid="tool-permission-context">
      {ctx.role && (
        <>
          <strong className="mono">{ctx.role}</strong> wants{" "}
        </>
      )}
      {ctx.permission && (
        <>
          permission <strong className="mono">{ctx.permission}</strong>
        </>
      )}
      {ctx.patterns.length > 0 && (
        <>
          {" "}on <span className="mono">{ctx.patterns.join(", ")}</span>
        </>
      )}
    </p>
  );
}

export function ToolPermissionButtons({
  busy,
  onChoose,
}: {
  busy: ToolPermissionChoice | null;
  onChoose: (choice: ToolPermissionChoice) => void;
}) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <Button onClick={() => onChoose("once")} disabled={busy !== null}>
        {busy === "once" ? "Allowing…" : "Allow once"}
      </Button>
      <Button
        variant="outline"
        onClick={() => onChoose("run")}
        disabled={busy !== null}
        title="Stop asking for this permission for the rest of this run"
      >
        {busy === "run" ? "Allowing…" : "Always (this run)"}
      </Button>
      <Button variant="destructive" onClick={() => onChoose("reject")} disabled={busy !== null}>
        {busy === "reject" ? "Rejecting…" : "Reject"}
      </Button>
    </div>
  );
}
