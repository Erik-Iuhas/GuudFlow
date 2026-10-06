import { useEffect, useState } from "react";
import { getToken } from "../api";
import type { DesksData } from "../types";
import { cn } from "../lib/utils";

export interface GitCommit {
  sha: string;
  short_sha: string;
  subject: string;
  author_role: string;
  branch: string;
  committed_at: number;
  stats: { files: number; insertions: number; deletions: number };
  parent_shas: string[];
}

interface Props {
  runId: string;
  desksData?: DesksData | null;
  onCommitClick?: (sha: string) => void;
}

// Branch palette from the status/column tokens. The full class names are
// spelled out so Tailwind can see them.
const BRANCH_TONES = [
  { stroke: "stroke-status-running", fill: "fill-status-running" },
  { stroke: "stroke-status-waiting", fill: "fill-status-waiting" },
  { stroke: "stroke-status-complete", fill: "fill-status-complete" },
  { stroke: "stroke-status-blocked", fill: "fill-status-blocked" },
  { stroke: "stroke-status-failed", fill: "fill-status-failed" },
  { stroke: "stroke-col-todo", fill: "fill-col-todo" },
  { stroke: "stroke-primary", fill: "fill-primary" },
  { stroke: "stroke-foreground", fill: "fill-foreground" },
];

const ROW_H = 40;
const NODE_R = 7;
const LEFT_LABEL_W = 120;
const COMMIT_SPACING = 60;
const PADDING = 16;

export function GitGraph({ runId, desksData, onCommitClick }: Props) {
  const [commits, setCommits] = useState<GitCommit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    const token = getToken();
    fetch(`/api/runs/${encodeURIComponent(runId)}/git/commits`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<GitCommit[]>;
      })
      .then(setCommits)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [runId]);

  if (loading) return <div className="p-3 text-xs text-muted-foreground">Loading git history…</div>;
  if (error) return <div className="p-3 text-xs text-muted-foreground">No git workspace yet.</div>;
  if (commits.length === 0) return <div className="p-3 text-xs text-muted-foreground">No commits yet.</div>;

  // Derive unique branches, keeping insertion order from commits
  const branchOrder: string[] = [];
  const branchSet = new Set<string>();
  for (const c of commits) {
    if (c.branch && !branchSet.has(c.branch)) {
      branchSet.add(c.branch);
      branchOrder.push(c.branch);
    }
  }
  if (branchOrder.length === 0) branchOrder.push("main");

  const branchIndex = new Map(branchOrder.map((b, i) => [b, i]));
  const branchTone = (branch: string) => BRANCH_TONES[(branchIndex.get(branch) ?? 0) % BRANCH_TONES.length];

  // Map sha → commit for merge line lookup
  const byShaSorted = new Map(commits.map((c) => [c.sha, c]));

  // x position per commit (index in sorted array)
  const commitX = new Map(commits.map((c, i) => [c.sha, LEFT_LABEL_W + PADDING + i * COMMIT_SPACING]));

  // Group commits by branch for sequential edge rendering
  const commitsByBranch = new Map<string, GitCommit[]>();
  for (const b of branchOrder) commitsByBranch.set(b, []);
  for (const c of commits) {
    commitsByBranch.get(c.branch)?.push(c);
  }

  const svgW = LEFT_LABEL_W + PADDING + commits.length * COMMIT_SPACING + PADDING;
  const svgH = branchOrder.length * ROW_H + PADDING * 2;

  // Closed-by-task lookup: sha → task goal
  const closedByMap = new Map<string, string>();
  if (desksData) {
    for (const task of Object.values(desksData.tasksById)) {
      if (task.closed_by_commit) {
        closedByMap.set(task.closed_by_commit, task.goal);
      }
    }
  }

  const rowY = (branch: string) => PADDING + (branchIndex.get(branch) ?? 0) * ROW_H + ROW_H / 2;

  return (
    <div className="overflow-x-auto">
      <svg width={svgW} height={svgH} role="img" aria-label="Git branch timeline">
        {/* Branch label lines */}
        {branchOrder.map((branch) => (
          <g key={branch}>
            <text
              x={LEFT_LABEL_W - 8}
              y={rowY(branch) + 4}
              textAnchor="end"
              fontSize={11}
              className={branchTone(branch).fill}
              fontWeight={600}
            >
              {branch.replace("agent/", "")}
            </text>
            <line
              x1={LEFT_LABEL_W}
              y1={rowY(branch)}
              x2={svgW - PADDING}
              y2={rowY(branch)}
              className={branchTone(branch).stroke}
              strokeWidth={1}
              strokeOpacity={0.25}
            />
          </g>
        ))}

        {/* Sequential commit lines within each branch */}
        {[...commitsByBranch.entries()].map(([branch, bCommits]) =>
          bCommits.slice(0, -1).map((c, i) => {
            const next = bCommits[i + 1];
            return (
              <line
                key={`seq-${c.sha}-${next.sha}`}
                x1={commitX.get(c.sha) ?? 0}
                y1={rowY(branch)}
                x2={commitX.get(next.sha) ?? 0}
                y2={rowY(branch)}
                className={branchTone(branch).stroke}
                strokeWidth={2}
              />
            );
          })
        )}

        {/* Cross-branch lines: merges (≥2 parents) AND branch-off points
            (first commit on a branch whose parent lives on another row). */}
        {commits.map((c) => {
          return c.parent_shas.map((pSha) => {
            const parent = byShaSorted.get(pSha);
            if (!parent || parent.branch === c.branch) return null;
            const x1 = commitX.get(pSha) ?? 0;
            const y1 = rowY(parent.branch);
            const x2 = commitX.get(c.sha) ?? 0;
            const y2 = rowY(c.branch);
            return (
              <path
                key={`merge-${c.sha}-${pSha}`}
                d={`M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`}
                fill="none"
                className={branchTone(c.branch).stroke}
                strokeWidth={1.5}
                strokeOpacity={0.5}
              />
            );
          });
        })}

        {/* Commit nodes */}
        {commits.map((c) => {
          const cx = commitX.get(c.sha) ?? 0;
          const cy = rowY(c.branch);
          const tone = branchTone(c.branch);
          const isHovered = hovered === c.sha;
          const closedTask = closedByMap.get(c.sha);
          return (
            <g
              key={c.sha}
              className={onCommitClick ? "cursor-pointer" : "cursor-default"}
              onClick={() => onCommitClick?.(c.sha)}
              onMouseEnter={() => setHovered(c.sha)}
              onMouseLeave={() => setHovered(null)}
            >
              <circle
                cx={cx}
                cy={cy}
                r={NODE_R + (isHovered ? 2 : 0)}
                className={cn(tone.fill, "stroke-card")}
                strokeWidth={2}
              />
              {closedTask && (
                <text x={cx} y={cy - NODE_R - 4} textAnchor="middle" fontSize={9} className={tone.fill}>✓</text>
              )}
              {isHovered && (
                <g>
                  <rect
                    x={cx - 80}
                    y={cy - 52}
                    width={160}
                    height={46}
                    rx={4}
                    className="fill-popover stroke-border"
                  />
                  <text x={cx} y={cy - 38} textAnchor="middle" fontSize={11} className="fill-popover-foreground" fontWeight={600}>
                    {c.subject.length > 22 ? c.subject.slice(0, 22) + "…" : c.subject}
                  </text>
                  <text x={cx} y={cy - 24} textAnchor="middle" fontSize={10} className="fill-muted-foreground">
                    {c.author_role} · {c.short_sha}
                  </text>
                  <text x={cx} y={cy - 12} textAnchor="middle" fontSize={10} className="fill-status-complete">
                    +{c.stats.insertions} <tspan className="fill-status-failed">-{c.stats.deletions}</tspan>
                  </text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
