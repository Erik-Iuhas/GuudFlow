import { useState } from "react";
import type { Signal } from "../types";
import {
  signalsForRoleIteration,
  type AgentLiveSummary,
} from "../lib/iterations";

/**
 * Phase G.1 — compact agent status card. Replaces the
 * "scan the BoardFeed linearly" experience for the
 * "what's each agent doing right now?" question.
 *
 * Expanded view shows the iteration's signal stream so the
 * user doesn't lose access to detail.
 */
export function AgentStateCard({
  summary,
  allSignals,
}: {
  summary: AgentLiveSummary;
  allSignals: Signal[];
}) {
  const [expanded, setExpanded] = useState(false);
  const iterationSignals = expanded
    ? signalsForRoleIteration(allSignals, summary.role, summary.iteration)
    : [];

  return (
    <div className={`agent-state-card agent-state-${summary.state}`}>
      <div
        className="row"
        style={{ alignItems: "center", cursor: "pointer", gap: 8 }}
        onClick={() => setExpanded((v) => !v)}
      >
        <span className={`tag tag-status-${summary.state}`}>{summary.state}</span>
        <strong className="mono">{summary.role}</strong>
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
        <span style={{ marginLeft: "auto" }} className="muted small">
          {expanded ? "▾" : "▸"}
        </span>
      </div>

      {expanded && (
        <div className="agent-state-detail">
          {iterationSignals.length === 0 ? (
            <div className="muted small">
              No signals from this agent in iteration {summary.iteration} yet.
            </div>
          ) : (
            <ul className="agent-iteration-list">
              {iterationSignals.map((s) => (
                <li key={s.id}>
                  <span className="tag small">{s.type}</span> {s.summary}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
