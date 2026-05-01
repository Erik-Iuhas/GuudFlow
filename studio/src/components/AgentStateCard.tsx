import { type ReactNode, useState } from "react";
import type { Signal } from "../types";
import {
  signalsForRoleIteration,
  type AgentLiveSummary,
} from "../lib/iterations";
import { avatarFor } from "../lib/avatar";

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
  const iterationSignals = expanded
    ? signalsForRoleIteration(allSignals, summary.role, summary.iteration)
    : [];

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
      className={`agent-state-card agent-state-${summary.state}`}
      style={{ cursor: onClick ? "pointer" : undefined }}
      onClick={onClick}
    >
      <div
        className="row"
        style={{ alignItems: "center", gap: 8 }}
        onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
      >
        {(() => {
          const seed = personaId ?? summary.role;
          const av = avatarFor(seed);
          return (
            <div
              style={{
                background: av.gradient, width: 20, height: 20, borderRadius: 4,
                fontSize: 8, fontWeight: 700, color: "#fff", flexShrink: 0,
                display: "flex", alignItems: "center", justifyContent: "center",
              }}
              aria-hidden
            >
              {av.initials}
            </div>
          );
        })()}
        <span className={`tag tag-status-${summary.state}`}>{summary.state}</span>
        <strong className="mono">{summary.role}</strong>
        {personaName && personaName !== summary.role && (
          <span className="muted small" style={{ fontStyle: "italic" }}>{personaName}</span>
        )}
        <span className="muted small">iter {summary.iteration}</span>
        {summary.lastCommit && (
          <span className="muted small mono" title={summary.lastCommitMessage ?? ""}>
            commit {summary.lastCommit}
          </span>
        )}
        {summary.claimedFiles.length > 0 && (
          <span className="muted small">
            claimed: {summary.claimedFiles.slice(0, 2).join(", ")}
            {summary.claimedFiles.length > 2 &&
              ` +${summary.claimedFiles.length - 2}`}
          </span>
        )}
        {summary.unreadComments > 0 && (
          <span className="tag tag-comments">
            {summary.unreadComments} unread comments
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          {summary.state === "failed" && onRetry && (
            <button
              className="primary small"
              disabled={retrying}
              onClick={handleRetry}
              title="Re-enqueue this run — completed agents will be skipped"
            >
              {retrying ? "…" : "Retry"}
            </button>
          )}
          <span className="muted small">{expanded ? "▾" : "▸"}</span>
        </div>
      </div>

      {expanded && (
        <div className="agent-state-detail" onClick={(e) => e.stopPropagation()}>
          {children}
          <details style={{ marginTop: 8 }}>
            <summary className="small muted" style={{ cursor: "pointer", userSelect: "none" }}>
              {iterationSignals.length > 0
                ? `${iterationSignals.length} signal${iterationSignals.length !== 1 ? "s" : ""}`
                : "signals"}
            </summary>
            {iterationSignals.length === 0 ? (
              <div className="muted small" style={{ paddingLeft: 12, marginTop: 4 }}>
                No signals in iteration {summary.iteration} yet.
              </div>
            ) : (
              <ul className="agent-iteration-list" style={{ marginTop: 4 }}>
                {iterationSignals.map((s) => (
                  <li key={s.id}>
                    <span className="tag small">{s.type}</span> {s.summary}
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
