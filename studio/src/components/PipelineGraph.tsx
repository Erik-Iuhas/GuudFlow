import { useMemo, useState } from "react";
import type { AgentConfig } from "../types";
import type { AgentLiveSummary, AgentLiveState } from "../lib/iterations";
import type { GraphData } from "../hooks/useRunGraph";

/**
 * DAG view of project agents.
 *
 * Two render modes:
 *  - Legacy (no graphData): edges from cfg.depends_on, layered depth layout.
 *  - Hierarchy (graphData present): 3 edge kinds, banded archetype layout.
 *
 * Used in Studio (config preview) and Run (live state).
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
  blocked: { fill: "#fef9c3", stroke: "#ca8a04", text: "#713f12" },
};

// Archetype band order (top → bottom)
const BAND_ORDER = ["planner", "specialist", "reviewer", "escalation_handler", ""];

interface Props {
  agents: AgentConfig[];
  liveStates?: Map<string, AgentLiveSummary>;
  onClickAgent?: (role: string) => void;
  onRemoveAgent?: (role: string) => void;
  graphData?: GraphData;
}

export function PipelineGraph({ agents, liveStates, onClickAgent, onRemoveAgent, graphData }: Props) {
  if (agents.length === 0) {
    return (
      <div className="muted small" style={{ padding: 12 }}>
        No agents to show. Add one to see the pipeline graph.
      </div>
    );
  }

  if (graphData) {
    return (
      <HierarchyGraph
        graphData={graphData}
        liveStates={liveStates}
        onClickAgent={onClickAgent}
      />
    );
  }

  const layout = computeLayout(agents);
  const width = layout.cols * NODE_W + (layout.cols - 1) * COL_GAP + PADDING * 2;
  const height =
    layout.maxRowsInCol * NODE_H + (layout.maxRowsInCol - 1) * ROW_GAP + PADDING * 2;

  const isConfigMode = !liveStates;

  return (
    <div className="pipeline-graph">
      <svg width={width} height={height} role="img" aria-label="Pipeline graph">
        <defs>
          {Object.entries(LEGACY_EDGE_STYLES).map(([kind, s]) => (
            <marker key={kind} id={`arrow-${kind}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={s.stroke} />
            </marker>
          ))}
        </defs>
        {layout.edges.map((e, i) => {
          const s = LEGACY_EDGE_STYLES[e.kind];
          return (
            <line
              key={i}
              x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
              stroke={s.stroke}
              strokeWidth={1.5}
              strokeDasharray={s.dash || undefined}
              markerEnd={`url(#arrow-${e.kind})`}
            />
          );
        })}
        {layout.nodes.map((n) => {
          const agent = agents.find((a) => a.role === n.role);
          let c: { fill: string; stroke: string; text: string };
          if (isConfigMode) {
            c = TIER_NODE[agent?.model_tier ?? "solid"] ?? TIER_NODE.solid;
          } else {
            const state = (liveStates?.get(n.role)?.state as AgentLiveState) ?? "pending";
            c = STATE_COLORS[state];
          }
          const sublabel = isConfigMode
            ? (agent?.model_tier ?? "solid")
            : ((liveStates?.get(n.role)?.state as AgentLiveState) ?? "pending");
          return (
            <g key={n.role} transform={`translate(${n.x}, ${n.y})`} style={{ cursor: onClickAgent ? "pointer" : "default" }} onClick={onClickAgent ? () => onClickAgent(n.role) : undefined}>
              <rect width={NODE_W} height={NODE_H} rx={8} ry={8} fill={c.fill} stroke={c.stroke} strokeWidth={2} />
              <text x={NODE_W / 2} y={24} textAnchor="middle" fontSize={13} fontWeight={600} fill={c.text}>{n.role}</text>
              <text x={NODE_W / 2} y={42} textAnchor="middle" fontSize={11} fill={c.text}>{sublabel}</text>
              {isConfigMode && onRemoveAgent && (
                <g
                  style={{ cursor: "pointer" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (confirm(`Remove agent ${n.role}?`)) onRemoveAgent(n.role);
                  }}
                >
                  <circle cx={NODE_W - 10} cy={10} r={9} fill="#ef4444" stroke="#fff" strokeWidth={1.5} />
                  <text x={NODE_W - 10} y={14} textAnchor="middle" fontSize={12} fontWeight={700} fill="#fff">×</text>
                </g>
              )}
            </g>
          );
        })}
      </svg>
      {isConfigMode && (
        <div className="row" style={{ gap: 12, padding: "4px 8px", flexWrap: "wrap" }}>
          {Object.entries(LEGACY_EDGE_STYLES).map(([kind, s]) => (
            <span key={kind} className="row" style={{ gap: 4, alignItems: "center", fontSize: 11, color: "#64748b" }}>
              <svg width={28} height={10}>
                <line x1={0} y1={5} x2={28} y2={5} stroke={s.stroke} strokeWidth={1.5} strokeDasharray={s.dash || undefined} />
              </svg>
              {s.label}
            </span>
          ))}
          {Object.entries(TIER_NODE).map(([tier, c]) => (
            <span key={tier} className="row" style={{ gap: 4, alignItems: "center", fontSize: 11, color: "#64748b" }}>
              <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 3, background: c.fill, border: `1.5px solid ${c.stroke}` }} />
              {tier}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Hierarchy render (graphData present) ─────────────────────────────────────

const EDGE_STYLES = {
  depends_on:     { stroke: "#6366f1", strokeDasharray: "none", label: "depends on" },
  can_assign_to:  { stroke: "#0ea5e9", strokeDasharray: "6 3",  label: "can assign to" },
  escalates_to:   { stroke: "#ef4444", strokeDasharray: "3 3",  label: "escalates to" },
} as const;

type EdgeKind = "depends_on" | "can_assign_to" | "escalates_to";

const TABS: { kind: EdgeKind; label: string }[] = [
  { kind: "depends_on",    label: "Flow" },
  { kind: "can_assign_to", label: "Assignments" },
  { kind: "escalates_to",  label: "Escalation" },
];

function HierarchyGraph({
  graphData,
  liveStates,
  onClickAgent,
}: {
  graphData: GraphData;
  liveStates?: Map<string, AgentLiveSummary>;
  onClickAgent?: (role: string) => void;
}) {
  const { nodes, edges } = graphData;
  const [activeKind, setActiveKind] = useState<EdgeKind>("depends_on");
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);

  // Group nodes by archetype band
  const bands = new Map<string, typeof nodes>();
  for (const n of nodes) {
    const band = BAND_ORDER.includes(n.archetype) ? n.archetype : "";
    if (!bands.has(band)) bands.set(band, []);
    bands.get(band)!.push(n);
  }

  const orderedBands = BAND_ORDER.filter((b) => bands.has(b));
  const positions = new Map<string, { x: number; y: number }>();

  const NODE_H_PERSONA = NODE_H + 14; // extra row for persona name
  const bandHeight = NODE_H_PERSONA + ROW_GAP;
  let currentY = PADDING;

  for (const band of orderedBands) {
    const bandNodes = (bands.get(band) ?? []).slice().sort((a, b) => a.role.localeCompare(b.role));
    const maxNodes = Math.max(...orderedBands.map((b) => (bands.get(b) ?? []).length));
    const maxW = maxNodes * NODE_W + (maxNodes - 1) * COL_GAP;
    const totalW = bandNodes.length * NODE_W + (bandNodes.length - 1) * COL_GAP;
    const startX = PADDING + (maxW - totalW) / 2;
    bandNodes.forEach((n, i) => {
      positions.set(n.role, { x: startX + i * (NODE_W + COL_GAP), y: currentY });
    });
    currentY += bandHeight;
  }

  const maxNodes = Math.max(...orderedBands.map((b) => (bands.get(b) ?? []).length), 1);
  const svgW = maxNodes * NODE_W + (maxNodes - 1) * COL_GAP + PADDING * 2;
  const svgH = orderedBands.length * bandHeight + PADDING * 2;

  const visibleEdges = edges.filter((e) => e.kind === activeKind);
  const activeStyle = EDGE_STYLES[activeKind];

  // Roots = nodes with no incoming edge of the active kind (execution starts here)
  const roots = useMemo(() => {
    const targets = new Set(edges.filter((e) => e.kind === activeKind).map((e) => e.to_role));
    return new Set(nodes.filter((n) => !targets.has(n.role)).map((n) => n.role));
  }, [nodes, edges, activeKind]);

  return (
    <div className="pipeline-graph" style={{ position: "relative" }}>
      {/* Tab strip */}
      <div className="row" style={{ gap: 4, padding: "6px 8px", borderBottom: "1px solid #e2e8f0" }}>
        {TABS.map((tab) => {
          const hasEdges = edges.some((e) => e.kind === tab.kind);
          const isActive = activeKind === tab.kind;
          const style = EDGE_STYLES[tab.kind];
          return (
            <button
              key={tab.kind}
              onClick={() => setActiveKind(tab.kind)}
              style={{
                padding: "3px 10px",
                borderRadius: 12,
                border: `1px solid ${style.stroke}`,
                background: isActive ? style.stroke : "transparent",
                color: isActive ? "#fff" : style.stroke,
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
                opacity: hasEdges ? 1 : 0.4,
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <svg width={svgW} height={svgH} role="img" aria-label="Hierarchy graph">
        <defs>
          <marker id={`arrow-active`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill={activeStyle.stroke} />
          </marker>
        </defs>

        {visibleEdges.map((e, i) => {
          const src = positions.get(e.from_role);
          const tgt = positions.get(e.to_role);
          if (!src || !tgt) return null;
          const x1 = src.x + NODE_W / 2;
          const y1 = src.y + NODE_H_PERSONA;
          const x2 = tgt.x + NODE_W / 2;
          const y2 = tgt.y;
          const midY = (y1 + y2) / 2;
          return (
            <path
              key={i}
              className={`edge-${e.kind}`}
              d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
              fill="none"
              stroke={activeStyle.stroke}
              strokeWidth={1.5}
              strokeDasharray={activeStyle.strokeDasharray === "none" ? undefined : activeStyle.strokeDasharray}
              markerEnd="url(#arrow-active)"
              style={{ cursor: "default" }}
              onMouseEnter={(ev) => {
                const svg = (ev.target as SVGElement).closest("svg");
                const rect = svg?.getBoundingClientRect();
                if (rect) setTooltip({ x: ev.clientX - rect.left, y: ev.clientY - rect.top - 24, text: activeStyle.label });
              }}
              onMouseLeave={() => setTooltip(null)}
            />
          );
        })}

        {nodes.map((n) => {
          const pos = positions.get(n.role);
          if (!pos) return null;
          const state = (liveStates?.get(n.role)?.state as AgentLiveState) ?? "pending";
          const c = STATE_COLORS[state];
          const showPersona = n.persona_name && n.persona_name !== n.role;
          return (
            <g
              key={n.role}
              transform={`translate(${pos.x}, ${pos.y})`}
              style={{ cursor: onClickAgent ? "pointer" : "default" }}
              onClick={onClickAgent ? () => onClickAgent(n.role) : undefined}
            >
              <rect width={NODE_W} height={NODE_H_PERSONA} rx={8} ry={8} fill={c.fill} stroke={c.stroke} strokeWidth={roots.has(n.role) ? 3 : 2} />
              {roots.has(n.role) && (
                <g>
                  <rect x={4} y={4} width={40} height={14} rx={4} fill="#10b981" />
                  <text x={24} y={14} textAnchor="middle" fontSize={8} fontWeight={700} fill="#fff">▶ START</text>
                </g>
              )}
              <text x={NODE_W / 2} y={24} textAnchor="middle" fontSize={13} fontWeight={600} fill={c.text}>{n.role}</text>
              <text x={NODE_W / 2} y={40} textAnchor="middle" fontSize={10} fill={c.text} opacity={0.75}>{state}</text>
              {showPersona && (
                <text x={NODE_W / 2} y={56} textAnchor="middle" fontSize={10} fill={c.text} opacity={0.6} fontStyle="italic">{n.persona_name}</text>
              )}
            </g>
          );
        })}

        {tooltip && (
          <g>
            <rect x={tooltip.x - 4} y={tooltip.y - 14} width={tooltip.text.length * 7 + 8} height={20} rx={4} fill="rgba(0,0,0,.7)" />
            <text x={tooltip.x} y={tooltip.y} fill="#fff" fontSize={11}>{tooltip.text}</text>
          </g>
        )}
      </svg>
    </div>
  );
}

// ── Legacy layout ────────────────────────────────────────────────────────────

interface LayoutNode { role: string; x: number; y: number; col: number; row: number; }
interface LayoutEdge { x1: number; y1: number; x2: number; y2: number; kind: "depends_on" | "manages" | "escalates_to"; }
interface Layout { nodes: LayoutNode[]; edges: LayoutEdge[]; cols: number; maxRowsInCol: number; }

const LEGACY_EDGE_STYLES = {
  depends_on:   { stroke: "#6366f1", dash: "",        label: "depends on" },
  manages:      { stroke: "#0ea5e9", dash: "6 3",     label: "manages" },
  escalates_to: { stroke: "#ef4444", dash: "3 3",     label: "escalates to" },
};

const TIER_NODE: Record<string, { fill: string; stroke: string; text: string }> = {
  heavy: { fill: "#fce7f3", stroke: "#ec4899", text: "#831843" },
  solid: { fill: "#e0e7ff", stroke: "#6366f1", text: "#3730a3" },
  fast:  { fill: "#d1fae5", stroke: "#10b981", text: "#065f46" },
};

function computeLayout(agents: AgentConfig[]): Layout {
  const byRole = new Map(agents.map((a) => [a.role, a]));
  const depth = new Map<string, number>();
  function compute(role: string, visiting: Set<string>): number {
    if (depth.has(role)) return depth.get(role)!;
    if (visiting.has(role)) return 0;
    visiting.add(role);
    const a = byRole.get(role);
    const deps = a?.depends_on ?? [];
    const d = deps.length === 0 ? 0 : 1 + Math.max(...deps.map((dep) => compute(dep, visiting)));
    visiting.delete(role);
    depth.set(role, d);
    return d;
  }
  for (const a of agents) compute(a.role, new Set());

  const byCol = new Map<number, string[]>();
  for (const a of agents) {
    const c = depth.get(a.role) ?? 0;
    let bucket = byCol.get(c);
    if (!bucket) { bucket = []; byCol.set(c, bucket); }
    bucket.push(a.role);
  }
  const cols = Math.max(...depth.values(), 0) + 1;
  const maxRowsInCol = Math.max(...Array.from(byCol.values()).map((b) => b.length));

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

  const edges: LayoutEdge[] = [];
  for (const a of agents) {
    const target = positions.get(a.role);
    if (!target) continue;
    // depends_on edges — horizontal connector between columns
    for (const dep of a.depends_on ?? []) {
      const source = positions.get(dep);
      if (!source) continue;
      edges.push({ x1: source.x + NODE_W, y1: source.y + NODE_H / 2, x2: target.x - 2, y2: target.y + NODE_H / 2, kind: "depends_on" });
    }
    // manages edges — diagonal downward
    for (const managed of a.manages ?? []) {
      const tgt = positions.get(managed);
      if (!tgt) continue;
      edges.push({ x1: target.x + NODE_W / 2, y1: target.y + NODE_H, x2: tgt.x + NODE_W / 2, y2: tgt.y - 2, kind: "manages" });
    }
    // escalates_to edges — diagonal upward
    for (const esc of a.escalates_to ?? []) {
      const tgt = positions.get(esc);
      if (!tgt) continue;
      edges.push({ x1: target.x + NODE_W / 2, y1: target.y + NODE_H, x2: tgt.x + NODE_W / 2, y2: tgt.y - 2, kind: "escalates_to" });
    }
  }
  return { nodes, edges, cols, maxRowsInCol };
}
