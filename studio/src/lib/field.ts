// Shared class strings for the form controls and callouts of the legacy views
// (U4). Everything is built from design tokens; there are no raw colours here.
import type { ModelTier } from "../types";

/** Text input, textarea and select. */
export const fieldCls =
  "w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-60";

/** Checkbox / radio (overrides the legacy full-width input rule). */
export const checkCls = "m-0 h-4 w-4 shrink-0 p-0 accent-primary";

/** Block label above a field. */
export const labelCls = "mb-1 block text-xs font-medium text-muted-foreground";

/** Inline label that wraps a checkbox. */
export const checkLabelCls = "mb-0 flex cursor-pointer items-center gap-2 text-xs font-medium text-foreground";

/** Callouts. */
export const errorCls =
  "mb-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive";
export const successCls =
  "mb-3 rounded-md border border-status-complete/30 bg-status-complete/10 px-3 py-2 text-sm text-status-complete";
export const warnCls =
  "rounded-md border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-sm text-status-blocked";

/** Small uppercase section heading. */
export const sectionHeadingCls = "m-0 mb-1.5 text-xs font-semibold uppercase text-muted-foreground";

/** Page h1 / h2. */
export const h1Cls = "m-0 mb-4 text-[22px] font-semibold tracking-tight";
export const h2Cls = "m-0 mb-2 mt-4 text-base font-semibold";

/** Model tier to Badge variant (heavy purple, solid blue, fast green). */
export function tierVariant(tier: ModelTier | string | undefined): "waiting" | "running" | "complete" {
  if (tier === "heavy") return "waiting";
  if (tier === "fast") return "complete";
  return "running";
}

/** Run status to Badge variant. */
export function runStatusVariant(status: string): "complete" | "running" | "failed" | "waiting" {
  if (status === "complete") return "complete";
  if (status === "running") return "running";
  if (status === "failed") return "failed";
  return "waiting";
}
