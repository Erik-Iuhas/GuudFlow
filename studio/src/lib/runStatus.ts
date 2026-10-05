import type { BadgeProps } from "../components/ui/badge";

/** Run statuses after which no agent is working. */
const TERMINAL_STATUSES = new Set([
  "complete",
  "max_iterations",
  "stuck",
  "deadline_exceeded",
  "errored",
  "failed",
  "stopped",
]);

export function isTerminalStatus(status: string | undefined): boolean {
  return !!status && TERMINAL_STATUSES.has(status);
}

/** Badge colour for a run status (the label text always carries the meaning too). */
export function statusVariant(status: string): BadgeProps["variant"] {
  if (status === "complete") return "complete";
  if (status === "running") return "running";
  if (status === "stopped") return "blocked";
  if (isTerminalStatus(status)) return "failed";
  return "outline";
}
