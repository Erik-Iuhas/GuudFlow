import type { Signal } from "../types";

/**
 * Phase G.1 helpers — derive per-agent state from a flat signal list.
 *
 * The runner posts signals chronologically; each agent goes through
 * STARTED → (work signals) → COMPLETE/FAILED. The Run view consumes
 * a websocket-streamed signal list; these helpers turn that into the
 * per-agent rendering state the PipelineGraph + AgentStateCard need.
 */

export type AgentLiveState =
  | "pending"
  | "ready"
  | "running"
  | "complete"
  | "failed"
  | "waiting_for_human";

export interface AgentLiveSummary {
  role: string;
  state: AgentLiveState;
  iteration: number;
  lastCommit: string | null;        // short SHA
  lastCommitMessage: string | null;
  claimedFiles: string[];           // currently claimed by this role
  unreadComments: number;           // USER_COMMENT signals with this role NOT in seen_by
}

/**
 * Derive a per-agent summary from the signal list. Agents not yet
 * mentioned in any signal default to `pending` — when a STARTED
 * lands they flip to `running`, etc.
 */
export function summariseAgents(
  roles: string[],
  signals: Signal[],
): Map<string, AgentLiveSummary> {
  const out = new Map<string, AgentLiveSummary>();
  for (const role of roles) {
    out.set(role, {
      role,
      state: "pending",
      iteration: 0,
      lastCommit: null,
      lastCommitMessage: null,
      claimedFiles: [],
      unreadComments: 0,
    });
  }

  // Track open file claims (claim - release pairs) per role.
  const claimsByRole = new Map<string, Set<string>>();

  for (const sig of signals) {
    const role = sig.from_agent;
    if (!role || !out.has(role)) {
      // System-emitted signals (git, runner) don't change agent states
      // here. Continue scanning for their data anyway in case later
      // logic needs it — for now, just skip.
      continue;
    }
    const summary = out.get(role)!;

    if (sig.type === "STARTED") {
      summary.state = "running";
    } else if (sig.type === "COMPLETE" || sig.type === "APPROVED") {
      summary.state = "complete";
    } else if (sig.type === "FAILED" || sig.type === "REJECTED") {
      summary.state = "failed";
    } else if (sig.type === "HUMAN_NEEDED") {
      summary.state = "waiting_for_human";
    } else if (sig.type === "GIT_COMMIT") {
      const sha = extractCommitSha(sig.detail || sig.summary);
      if (sha) {
        summary.lastCommit = sha.slice(0, 7);
        summary.lastCommitMessage = sig.summary;
      }
    } else if (sig.type === "FILE_CLAIM") {
      const path = extractClaimedPath(sig.summary);
      if (path) {
        let set = claimsByRole.get(role);
        if (!set) {
          set = new Set();
          claimsByRole.set(role, set);
        }
        set.add(path);
      }
    } else if (sig.type === "FILE_RELEASE") {
      const path = extractClaimedPath(sig.summary);
      if (path) claimsByRole.get(role)?.delete(path);
    }

    if (sig.iteration > summary.iteration) {
      summary.iteration = sig.iteration;
    }
  }

  // Finalise claimed-files lists (snapshot of remaining unreleased claims).
  for (const [role, claims] of claimsByRole.entries()) {
    const summary = out.get(role);
    if (summary) summary.claimedFiles = Array.from(claims).sort();
  }

  // Count user comments not yet absorbed by each role.
  for (const sig of signals) {
    if (
      sig.type === "USER_COMMENT" ||
      sig.type === "USER_COMMENT_PRIORITY"
    ) {
      for (const summary of out.values()) {
        if (!sig.seen_by?.includes(summary.role)) {
          summary.unreadComments += 1;
        }
      }
    }
  }

  return out;
}

/** A signal's detail like "agent/foo @ a1b2c3d4: feat: x" or summary
 *  contains a short SHA. Try a few common formats. */
function extractCommitSha(text: string | null): string | null {
  if (!text) return null;
  const match = text.match(/\b([0-9a-f]{7,40})\b/);
  return match ? match[1] : null;
}

/** FILE_CLAIM summaries look like "claim: path/to/file.py". Extract
 *  the path. */
function extractClaimedPath(summary: string): string | null {
  const m = summary.match(/(?:claim|release)[:\s]+(.+)/i);
  return m ? m[1].trim() : null;
}

/**
 * For the AgentStateCard's hover-to-expand: the signals from a
 * specific iteration for a specific role, in chronological order.
 */
export function signalsForRoleIteration(
  signals: Signal[],
  role: string,
  iteration: number,
): Signal[] {
  return signals.filter(
    (s) => s.from_agent === role && s.iteration === iteration,
  );
}
