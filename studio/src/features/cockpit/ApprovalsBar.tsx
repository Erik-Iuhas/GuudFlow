import { useState } from "react";
import { ApiError } from "../../api";
import { useDecideApproval, useDecideToolPermission } from "../../api/queries";
import { ToolPermissionButtons, ToolPermissionSummary } from "../../components/harness/toolPermission";
import type { ToolPermissionChoice } from "../../components/harness/toolPermission";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { useRunStore } from "../../stores/runStoreContext";
import type { ApprovalView } from "../../stores/runEvents";
import { formatUsd } from "./model";

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : String(e);
}

function ApprovalItem({ runId, approval }: { runId: string; approval: ApprovalView }) {
  const decide = useDecideApproval(runId);
  const decideTool = useDecideToolPermission(runId);
  const [toolChoice, setToolChoice] = useState<ToolPermissionChoice | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isTool = approval.kind === "tool_permission";
  const busy = decide.isPending || decideTool.isPending;

  function chooseTool(choice: ToolPermissionChoice) {
    setToolChoice(choice);
    setError(null);
    decideTool.mutate(
      { approvalId: approval.id, choice },
      {
        onSuccess: (decision) => setSent(decision),
        onError: (e) => setError(errorText(e)),
        onSettled: () => setToolChoice(null),
      },
    );
  }

  function choose(decision: "grant" | "deny") {
    setError(null);
    decide.mutate(
      { approvalId: approval.id, decision },
      {
        onSuccess: (res) => setSent(res?.decision ?? (decision === "grant" ? "granted" : "denied")),
        onError: (e) => setError(errorText(e)),
      },
    );
  }

  return (
    <Card
      className="flex w-80 shrink-0 flex-col gap-1 border-status-blocked/40 bg-status-blocked/10 p-3 text-sm"
      data-testid="approval-item"
      data-approval-id={approval.id}
    >
      <div className="flex items-center gap-2">
        <Badge variant="blocked">{approval.kind}</Badge>
        {approval.role && <span className="font-mono text-xs text-muted-foreground">{approval.role}</span>}
        {approval.estimatedCostUsd != null && (
          <span className="ml-auto text-xs text-muted-foreground">~{formatUsd(approval.estimatedCostUsd)}</span>
        )}
      </div>
      <p className="m-0 line-clamp-2" title={approval.summary}>
        {approval.summary}
      </p>
      {isTool && <ToolPermissionSummary detail={approval.signal.detail} />}
      {error && (
        <div role="alert" className="text-xs text-destructive">
          {error}
        </div>
      )}
      {sent ? (
        <p className="m-0 text-xs text-muted-foreground" role="status">
          Decision sent ({sent}). Waiting for the run to pick it up.
        </p>
      ) : isTool ? (
        <ToolPermissionButtons busy={toolChoice} onChoose={chooseTool} />
      ) : (
        <div className="mt-1 flex items-center gap-2">
          <Button size="sm" onClick={() => choose("grant")} disabled={busy}>
            Grant
          </Button>
          <Button size="sm" variant="destructive" onClick={() => choose("deny")} disabled={busy}>
            Deny
          </Button>
        </div>
      )}
    </Card>
  );
}

/**
 * Pending approval gates. Tool-permission asks get once / always (this run) / reject; every
 * other gate gets grant / deny. The matching APPROVAL_* signal removes the item.
 */
export function ApprovalsBar({ runId }: { runId: string }) {
  const approvals = useRunStore((s) => s.approvals);
  return (
    <div data-approvals aria-label="Pending approvals" role="region" className="min-w-0">
      {approvals.length === 0 ? (
        <p className="m-0 text-xs text-muted-foreground">No approvals pending.</p>
      ) : (
        <div className="flex gap-2 overflow-x-auto pb-1">
          {approvals.map((a) => (
            <ApprovalItem key={a.id} runId={runId} approval={a} />
          ))}
        </div>
      )}
    </div>
  );
}
