import { useState } from "react";
import { ApiError, approvalsApi } from "../api";
import type { Signal } from "../types";

interface ApprovalContext {
  approval_id: string;
  kind: string;
  estimated_cost_usd?: number | null;
}

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

  return (
    <div className="card approval-card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <strong>Approval needed</strong>
          <span className="tag" style={{ marginLeft: 8 }}>{ctx.kind}</span>
        </div>
        <span className="muted small mono">{ctx.approval_id}</span>
      </div>

      <p style={{ marginTop: 8 }}>{signal.summary}</p>

      {ctx.estimated_cost_usd != null && (
        <p className="muted small">
          Estimated cost: <strong>${ctx.estimated_cost_usd.toFixed(2)}</strong>
        </p>
      )}

      <textarea
        rows={2}
        placeholder="Optional note (visible on the board)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        style={{ marginTop: 8 }}
        disabled={busy !== null}
      />

      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}

      <div className="row" style={{ marginTop: 8, gap: 8 }}>
        <button
          className="primary"
          onClick={() => void decide("grant")}
          disabled={busy !== null}
        >
          {busy === "grant" ? "Granting…" : "Grant"}
        </button>
        <button
          className="danger"
          onClick={() => void decide("deny")}
          disabled={busy !== null}
        >
          {busy === "deny" ? "Denying…" : "Deny"}
        </button>
      </div>
    </div>
  );
}

/** Parse the runner's `detail` string into structured fields.
 *  Format: `approval_id=appr_abc; kind=heavy_tier_dispatch; estimated_cost_usd=0.42`
 */
function parseApprovalDetail(detail: string | null | undefined): ApprovalContext | null {
  if (!detail) return null;
  const fields = Object.fromEntries(
    detail.split(";")
      .map((kv) => kv.trim().split("=", 2))
      .filter((p) => p.length === 2)
      .map(([k, v]) => [k.trim(), v.trim()]),
  );
  if (!fields.approval_id) return null;
  const cost =
    fields.estimated_cost_usd && fields.estimated_cost_usd !== "None"
      ? parseFloat(fields.estimated_cost_usd)
      : null;
  return {
    approval_id: fields.approval_id,
    kind: fields.kind ?? "approval",
    estimated_cost_usd: Number.isFinite(cost as number) ? (cost as number) : null,
  };
}
