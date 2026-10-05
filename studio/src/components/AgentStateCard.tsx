import { type ReactNode, useState } from "react";
import type { Signal } from "../types";
import {
  signalsForRoleIteration,
  type AgentLiveState,
  type AgentLiveSummary,
} from "../lib/iterations";
import { avatarFor } from "../lib/avatar";
import { cn } from "../lib/utils";
import { latestCapabilityWarnings } from "./harness/parse";
import { Badge, type BadgeProps } from "./ui/badge";
import { Button } from "./ui/button";

/** Left-border accent per live state (status tokens). */
const STATE_BORDER: Record<AgentLiveState, string> = {
  pending: "border-l-muted-foreground/40",
  ready: "border-l-status-running/50",
  running: "border-l-status-running",
  complete: "border-l-status-complete",
  failed: "border-l-status-failed",
  waiting_for_human: "border-l-status-waiting",
  blocked: "border-l-status-blocked",
};

const STATE_BADGE: Record<AgentLiveState, BadgeProps["variant"]> = {
  pending: "secondary",
  ready: "secondary",
  running: "running",
  complete: "complete",
  failed: "failed",
  waiting_for_human: "waiting",
  blocked: "blocked",
};

export function AgentStateCard({
  summary,
  allSignals,
  onRetry,
  onClick,
  children,
  personaId,
  personaName,
}: {
  summary: AgentLiveSummary;
  allSignals: Signal[];
  onRetry?: () => void;
  onClick?: () => void;
  children?: ReactNode;
  personaId?: string | null;
  personaName?: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const capWarnings = latestCapabilityWarnings(allSignals, summary.role);
  const iterationSignals = expanded
    ? signalsForRoleIteration(allSignals, summary.role, summary.iteration)
    : [];
  const av = avatarFor(personaId ?? summary.role);

  async function handleRetry(e: React.MouseEvent) {
    e.stopPropagation();
    if (!onRetry) return;
    setRetrying(true);
    try {
      await onRetry();
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div
      className={cn(
        "mt-1 rounded-md border border-l-[3px] border-border bg-card px-3 py-2 text-card-foreground",
        STATE_BORDER[summary.state],
        onClick && "cursor-pointer",
      )}
      data-state={summary.state}
      onClick={onClick}
    >
      <div
        className="flex items-center gap-2"
        onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
      >
        {/* The gradient is generated per persona at runtime, so it cannot be a utility class:
            it is handed over as the `--avatar-bg` CSS variable and painted by a class. */}
        <div
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-[image:var(--avatar-bg)] text-[8px] font-bold text-white"
          style={{ "--avatar-bg": av.gradient } as React.CSSProperties}
          aria-hidden
        >
          {av.initials}
        </div>
        <Badge variant={STATE_BADGE[summary.state]}>{summary.state}</Badge>
        <strong className="font-mono">{summary.role}</strong>
        {personaName && personaName !== summary.role && (
          <span className="text-xs italic text-muted-foreground">{personaName}</span>
        )}
        <span className="text-xs text-muted-foreground">iter {summary.iteration}</span>
        {capWarnings.length > 0 && (
          <span
            role="img"
            aria-label="Capability warning"
            title={capWarnings.join("\n")}
            className="cursor-help text-status-blocked"
          >
            ⚠
          </span>
        )}
        {summary.lastCommit && (
          <span className="font-mono text-xs text-muted-foreground" title={summary.lastCommitMessage ?? ""}>
            commit {summary.lastCommit}
          </span>
        )}
        {summary.claimedFiles.length > 0 && (
          <span className="text-xs text-muted-foreground">
            claimed: {summary.claimedFiles.slice(0, 2).join(", ")}
            {summary.claimedFiles.length > 2 &&
              ` +${summary.claimedFiles.length - 2}`}
          </span>
        )}
        {summary.unreadComments > 0 && (
          <Badge variant="blocked">{summary.unreadComments} unread comments</Badge>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {summary.state === "failed" && onRetry && (
            <Button
              size="sm"
              disabled={retrying}
              onClick={handleRetry}
              title="Re-enqueue this run — completed agents will be skipped"
            >
              {retrying ? "…" : "Retry"}
            </Button>
          )}
          <span className="text-xs text-muted-foreground">{expanded ? "▾" : "▸"}</span>
        </div>
      </div>

      {expanded && (
        <div className="mt-2 border-t border-border pt-2" onClick={(e) => e.stopPropagation()}>
          {children}
          <details className="mt-2">
            <summary className="cursor-pointer select-none text-xs text-muted-foreground">
              {iterationSignals.length > 0
                ? `${iterationSignals.length} signal${iterationSignals.length !== 1 ? "s" : ""}`
                : "signals"}
            </summary>
            {iterationSignals.length === 0 ? (
              <div className="mt-1 pl-3 text-xs text-muted-foreground">
                No signals in iteration {summary.iteration} yet.
              </div>
            ) : (
              <ul className="m-0 mt-1 list-none p-0 text-xs text-muted-foreground">
                {iterationSignals.map((s) => (
                  <li key={s.id} className="py-0.5">
                    <Badge variant="secondary">{s.type}</Badge> {s.summary}
                  </li>
                ))}
              </ul>
            )}
          </details>
        </div>
      )}
    </div>
  );
}
