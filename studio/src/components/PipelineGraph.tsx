import type { AgentConfig } from "../types";
import type { AgentLiveSummary, AgentLiveState } from "../lib/iterations";

/**
 * Phase G.1 — DAG view of project agents with `depends_on` edges.
 *
 * Used in two places:
 *  - Studio (config preview) — pure depends_on layout, all nodes
 *    in `pending` state
 *  - Run (live state) — nodes coloured by `liveStates[role]`
 *
 * Layout: layered topological sort. Each agent is placed in a
 * column corresponding to its dependency depth from a root; rows
 * within a column are agents at the same depth. Hand-rolled SVG
 * keeps the dependency footprint zero. Good enough for ~15 agents;
 * if rosters grow we can swap in a layout library in G.6 polish.
 */

const NODE_W = 160;
const NODE_H = 60;
const COL_GAP = 80;
const ROW_GAP = 28;
const PADDING = 24;

const STATE_COLORS: Record<AgentLiveState, { fill: string; stroke: string; text: string }> = {
  pending: { fill: "#f1f5f9", stroke: "#cbd5e1", text: "#475569" },
  ready: { fill: "#e0e7ff", stroke: "#6366f1", text: "#3730a3" },
  running: { fill: "#fef3c7", stroke: "#f59e0b", text: "#92400e" },
  complete: { fill: "#d1fae5", stroke: "#10b981", text: "#065f46" },
  failed: { fill: "#fee2e2", stroke: "#ef4444", text: "#991b1b" },
  waiting_for_human: { fill: "#dbeafe", stroke: "#3b82f6", text: "#1e3a8a" },
};

interface Props {
  agents: AgentConfig[];
  liveStates?: Map<string, AgentLiveSummary>;
  onClickAgent?: (role: string) => void;
}

export function PipelineGraph({ agents, liveStates, onClickAgent }: Props) {
  if (agents.length === 0) {
    return (
      <div className="muted small" style={{ padding: 12 }}>
        No agents to show. Add one to see the pipeline graph.
      </div>
    );
  }

  const layout = computeLayout(agents);
  const width = layout.cols * NODE_W + (layout.cols - 1) * COL_GAP + PADDING * 2;
  const height =
    layout.maxRowsInCol * NODE_H + (layout.maxRowsInCol - 1) * ROW_GAP + PADDING * 2;

  return (
    <div className="pipeline-graph">
      <svg width={width} height={height} role="img" aria-label="Pipeline graph">
        {/* Edges first so nodes paint over them */}
        {layout.edges.map((e, i) => (
          <line
            key={i}
            x1={e.x1}
            y1={e.y1}
            x2={e.x2}
            y2={e.y2}
            stroke="#94a3b8"
            strokeWidth={1.5}
            markerEnd="url(#arrow)"
          />
        ))}
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>

        {layout.nodes.map((n) => {
          const state =
            (liveStates?.get(n.role)?.state as AgentLiveState) ?? "pending";
          const c = STATE_COLORS[state];
          return (
            <g
              key={n.role}
              transform={`translate(${n.x}, ${n.y})`}
              style={{ cursor: onClickAgent ? "pointer" : "default" }}
              onClick={onClickAgent ? () => onClickAgent(n.role) : undefined}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                ry={8}
                fill={c.fill}
                stroke={c.stroke}
                strokeWidth={2}
              />
              <text
                x={NODE_W / 2}
                y={24}
                textAnchor="middle"
                fontSize={13}
                fontWeight={600}
                fill={c.text}
              >
                {n.role}
              </text>
              <text
                x={NODE_W / 2}
                y={42}
                textAnchor="middle"
                fontSize={11}
                fill={c.text}
              >
                {state}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

interface LayoutNode {
  role: string;
  x: number;
  y: number;
  col: number;
  row: number;
}

interface LayoutEdge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface Layout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  cols: number;
  maxRowsInCol: number;
}

/**
 * Compute layered DAG layout. Each node's column = its longest path
 * from any root. Nodes within a column stack vertically, in input
 * order. Edges are straight lines from source's right edge to
 * target's left edge.
 */
function computeLayout(agents: AgentConfig[]): Layout {
  const byRole = new Map(agents.map((a) => [a.role, a]));

  // Compute column (depth) per role via memoised DFS.
  const depth = new Map<string, number>();
  function compute(role: string, visiting: Set<string>): number {
    if (depth.has(role)) return depth.get(role)!;
    if (visiting.has(role)) return 0; // cycle guard
    visiting.add(role);
    const a = byRole.get(role);
    const deps = a?.depends_on ?? [];
    const d =
      deps.length === 0
        ? 0
        : 1 + Math.max(...deps.map((dep) => compute(dep, visiting)));
    visiting.delete(role);
    depth.set(role, d);
    return d;
  }
  for (const a of agents) compute(a.role, new Set());

  // Bucket agents by column.
  const byCol = new Map<number, string[]>();
  for (const a of agents) {
    const c = depth.get(a.role) ?? 0;
    let bucket = byCol.get(c);
    if (!bucket) {
      bucket = [];
      byCol.set(c, bucket);
    }
    bucket.push(a.role);
  }
  const cols = (Math.max(...depth.values(), 0)) + 1;
  const maxRowsInCol = Math.max(...Array.from(byCol.values()).map((b) => b.length));

  // Compute node positions.
  const nodes: LayoutNode[] = [];
  const positions = new Map<string, { x: number; y: number }>();
  for (let c = 0; c < cols; c++) {
    const bucket = byCol.get(c) ?? [];
    bucket.forEach((role, row) => {
      const x = PADDING + c * (NODE_W + COL_GAP);
      const y = PADDING + row * (NODE_H + ROW_GAP);
      nodes.push({ role, x, y, col: c, row });
      positions.set(role, { x, y });
    });
  }

  // Compute edge segments.
  const edges: LayoutEdge[] = [];
  for (const a of agents) {
    const target = positions.get(a.role);
    if (!target) continue;
    for (const dep of a.depends_on ?? []) {
      const source = positions.get(dep);
      if (!source) continue;
      edges.push({
        x1: source.x + NODE_W,
        y1: source.y + NODE_H / 2,
        x2: target.x - 2,
        y2: target.y + NODE_H / 2,
      });
    }
  }

  return { nodes, edges, cols, maxRowsInCol };
}
