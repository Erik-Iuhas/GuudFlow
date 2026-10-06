/** Structured fields parsed from an APPROVAL_* signal's `detail` string. */
export interface ApprovalContext {
  approval_id: string;
  kind: string;
  estimated_cost_usd?: number | null;
}

/**
 * Parse the runner's `detail` string into structured fields.
 * Two formats exist: `approval_id=appr_abc; kind=heavy_tier_dispatch; estimated_cost_usd=0.42`
 * and a JSON object with the same keys. Returns null when there is no usable approval id.
 */
export function parseApprovalDetail(detail: string | null | undefined): ApprovalContext | null {
  if (!detail) return null;
  if (detail.trimStart().startsWith("{")) {
    try {
      const j = JSON.parse(detail) as Record<string, unknown>;
      if (typeof j.approval_id !== "string" || !j.approval_id) return null;
      const cost = typeof j.estimated_cost_usd === "number" ? j.estimated_cost_usd : null;
      return {
        approval_id: j.approval_id,
        kind: typeof j.kind === "string" ? j.kind : "approval",
        estimated_cost_usd: cost,
      };
    } catch {
      return null;
    }
  }
  const fields = Object.fromEntries(
    detail
      .split(";")
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
