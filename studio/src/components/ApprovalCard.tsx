import { useState } from "react";
import { ApiError, approvalsApi } from "../api";
import type { Signal } from "../types";
import { parseApprovalDetail } from "../lib/approvalDetail";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import {
  decideToolPermission,
  ToolPermissionButtons,
  ToolPermissionSummary,
  type ToolPermissionChoice,
} from "./harness/toolPermission";

/**
 * Inline gate prompt shown on the Run view when an APPROVAL_NEEDED
 * signal is unresolved. The user clicks Grant / Deny; the card POSTs
 * to /api/runs/{id}/approval/{id} which posts the matching
 * APPROVAL_GRANTED / APPROVAL_DENIED signal — the live WebSocket feed
 * picks it up and the card disappears on the next render.
 */
export interface ApprovalCardProps {
  runId: string;
  signal: Signal;
  /** Called after a successful decision so the parent can hide the card
   *  optimistically rather than waiting for the WS round-trip. */
  onDecided?: (decision: "granted" | "denied") => void;
}

export function ApprovalCard({ runId, signal, onDecided }: ApprovalCardProps) {
  const [busy, setBusy] = useState<"grant" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [toolBusy, setToolBusy] = useState<ToolPermissionChoice | null>(null);

  const ctx = parseApprovalDetail(signal.detail);
  if (ctx === null) {
    return null; // malformed detail — don't render a broken card
  }

  async function decide(decision: "grant" | "deny") {
    setBusy(decision);
    setError(null);
    try {
      const res = await approvalsApi.decide(
        runId,
        ctx!.approval_id,
        decision,
        note.trim() || undefined,
      );
      if (onDecided) onDecided(res.decision);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function decideTool(choice: ToolPermissionChoice) {
    setToolBusy(choice);
    setError(null);
    try {
      const decision = await decideToolPermission(
        runId,
        ctx!.approval_id,
        choice,
        note.trim() || undefined,
      );
      if (onDecided) onDecided(decision);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setToolBusy(null);
    }
  }

  const isToolPermission = ctx.kind === "tool_permission";

  return (
    <Card className="mt-2 border-status-blocked/40 bg-status-blocked/10 p-4" data-testid="approval-card">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <strong>Approval needed</strong>
          <Badge variant="blocked">{ctx.kind}</Badge>
        </div>
        <span className="font-mono text-xs text-muted-foreground">{ctx.approval_id}</span>
      </div>

      <p className="mt-2">{signal.summary}</p>

      {isToolPermission && <ToolPermissionSummary detail={signal.detail} />}

      {ctx.estimated_cost_usd != null && (
        <p className="text-xs text-muted-foreground">
          Estimated cost: <strong>${ctx.estimated_cost_usd.toFixed(2)}</strong>
        </p>
      )}

      <textarea
        rows={2}
        placeholder="Optional note: the agent reads it on its next turn"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        className="mt-2"
        disabled={busy !== null || toolBusy !== null}
      />

      {error && (
        <div
          role="alert"
          className="mt-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-sm text-destructive"
        >
          {error}
        </div>
      )}

      {isToolPermission ? (
        <ToolPermissionButtons busy={toolBusy} onChoose={(c) => void decideTool(c)} />
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <Button onClick={() => void decide("grant")} disabled={busy !== null}>
            {busy === "grant" ? "Granting…" : "Grant"}
          </Button>
          <Button variant="destructive" onClick={() => void decide("deny")} disabled={busy !== null}>
            {busy === "deny" ? "Denying…" : "Deny"}
          </Button>
        </div>
      )}
    </Card>
  );
}
