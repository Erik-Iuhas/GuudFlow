import { useMemo } from "react";
import {
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/base.css";
import { cn } from "../../lib/utils";
import { useRunStore } from "../../stores/runStoreContext";
import type { AgentState } from "../../stores/runEvents";
import { resultSummary } from "./AgentDetail";
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  STATE_LABEL,
  formatUsd,
  buildGraph,
  type EdgeKind,
  type GraphDirection,
  type RoleSpec,
} from "./model";

type RoleNodeData = {
  direction: GraphDirection;
  role: string;
  state: AgentState;
  pulsing: boolean;
  detached: boolean;
  card?: string;
  selected: boolean;
  onOpen?: (role: string) => void;
};
type RoleNodeType = Node<RoleNodeData, "role">;

// Colour comes from the --status-* tokens; the state is also written out as text.
const STATE_BOX: Record<AgentState, string> = {
  idle: "border-border bg-card",
  running: "border-status-running bg-status-running/10",
  complete: "border-status-complete bg-status-complete/10",
  failed: "border-status-failed bg-status-failed/10",
  blocked: "border-status-blocked bg-status-blocked/10",
  waiting: "border-status-waiting bg-status-waiting/10",
};
const STATE_TEXT: Record<AgentState, string> = {
  idle: "text-muted-foreground",
  running: "text-status-running",
  complete: "text-status-complete",
  failed: "text-status-failed",
  blocked: "text-status-blocked",
  waiting: "text-status-waiting",
};
const STATE_MARK: Record<AgentState, string> = {
  idle: "○",
  running: "●",
  complete: "✓",
  failed: "✕",
  blocked: "⏸",
  waiting: "?",
};

/**
 * One role: its state, current card, a two-line last result and calls · cost. The whole
 * node is a button that opens the role's tool calls and full result (the details sheet).
 */
function RoleNode({ data }: NodeProps<RoleNodeType>) {
  const { role } = data;
  const toolCalls = useRunStore((s) => s.agents[role]?.toolCalls ?? 0);
  const cost = useRunStore((s) => s.agents[role]?.cost ?? 0);
  const card = useRunStore((s) => {
    const id = s.agents[role]?.card;
    return id ? s.cards[id] : undefined;
  });
  const summary = useRunStore((s) => resultSummary(s, role));
  const name = useRunStore((s) => s.names[role]);
  const cardLine = data.detached && !card ? "overseer (detached)"
    : card ? `${card.id} · ${card.title}` : data.card ? `card ${data.card}` : "no active card";
  return (
    <div
      className={cn(
        "flex flex-col overflow-hidden rounded-md border-2 text-foreground",
        STATE_BOX[data.state],
        data.detached && "border-dashed",
        data.pulsing && "motion-safe:animate-pulse",
        data.selected && "ring-2 ring-ring",
      )}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
      data-testid={`graph-node-${role}`}
      data-state={data.state}
    >
      {/* Edge anchors only: the graph is read-only, so the dots stay invisible (O22). */}
      {handleGeometry(data.direction).map((h) => (
        <Handle key={h.id} id={h.id} type={h.type} position={h.position}
          className={cn("!pointer-events-none !opacity-0", HANDLE_OFFSET[h.id])} />
      ))}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          data.onOpen?.(role);
        }}
        aria-label={`${name ? `${name}, ${role}` : role}: ${STATE_LABEL[data.state]}. Show tool calls and result`}
        className="nodrag flex h-full w-full min-w-0 cursor-pointer flex-col gap-0.5 rounded-md px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex w-full items-center justify-between gap-2">
          <span className="min-w-0 truncate text-sm">
            <span className="font-semibold">{name ?? role}</span>
            {name && <span className="text-xs text-muted-foreground"> · {role}</span>}
          </span>
          <span className={cn("shrink-0 text-xs font-medium", STATE_TEXT[data.state])}>
            <span aria-hidden>{STATE_MARK[data.state]} </span>
            {STATE_LABEL[data.state]}
          </span>
        </span>
        <span className="w-full truncate text-xs text-muted-foreground" title={card?.title}>{cardLine}</span>
        <span className="line-clamp-2 w-full text-xs" title={summary}>
          {summary || <span className="text-muted-foreground">No result yet.</span>}
        </span>
        <span className="mt-auto flex w-full items-center justify-between text-[11px] text-muted-foreground">
          <span>{toolCalls} calls · {formatUsd(cost)}</span>
          <span aria-hidden>details ›</span>
        </span>
      </button>
    </div>
  );
}

const nodeTypes = { role: RoleNode };

const EDGE_CLASS: Record<EdgeKind, string> = {
  depends_on: "cockpit-edge-depends",
  review: "cockpit-edge-review",
  blocked: "cockpit-edge-blocked",
};
// react-flow draws markers with a CSS colour, so tokens go through hsl(var(...)).
const MARKER_COLOR: Record<EdgeKind, string> = {
  depends_on: "hsl(var(--muted-foreground))",
  review: "hsl(var(--status-waiting))",
  blocked: "hsl(var(--status-blocked))",
};
const SOURCE_HANDLE: Record<EdgeKind, string> = { depends_on: "out", review: "review-out", blocked: "blocked-out" };
const TARGET_HANDLE: Record<EdgeKind, string> = { depends_on: "in", review: "review-in", blocked: "blocked-in" };

export interface PipelineGraphProps {
  specs: readonly RoleSpec[];
  selectedRole?: string | null;
  onSelectRole?: (role: string) => void;
  className?: string;
  /** Layout direction. */
  direction?: GraphDirection;
  /** Size the graph's height to its nodes (at 1x) instead of filling the parent. */
  fitHeight?: boolean;
}

/**
 * The live pipeline (React Flow): role nodes coloured by agent state (with a text label),
 * solid depends_on edges, review edges (reviewer -> author) carrying the rework round,
 * dashed blocked-on arrows, and the overseer as a detached node that pulses while it runs.
 */
export function PipelineGraph({
  specs, selectedRole, onSelectRole, className, direction = "LR", fitHeight = false,
}: PipelineGraphProps) {
  const agents = useRunStore((s) => s.agents);
  const reviews = useRunStore((s) => s.reviews);
  const blockedOn = useRunStore((s) => s.blockedOn);

  const model = useMemo(() => buildGraph(specs, agents, reviews, blockedOn, direction),
    [specs, agents, reviews, blockedOn, direction]);

  const { nodes, edges } = useMemo(() => {
    const nodes: RoleNodeType[] = model.nodes.map((n) => ({
      id: n.id,
      type: "role",
      // React Flow requires an explicit position per node; it is computed in model.ts.
      position: { x: n.x, y: n.y },
      initialWidth: NODE_WIDTH,
      initialHeight: NODE_HEIGHT,
      // Handle geometry up front so edges draw on the first paint (and under jsdom).
      handles: handleGeometry(direction),
      ariaLabel: `${n.role}: ${STATE_LABEL[n.state]}`,
      data: {
        direction,
        role: n.role, state: n.state, pulsing: n.pulsing, detached: n.detached, card: n.card,
        selected: selectedRole === n.role,
        onOpen: onSelectRole,
      },
    }));
    const edges: Edge[] = model.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: SOURCE_HANDLE[e.kind],
      targetHandle: TARGET_HANDLE[e.kind],
      type: "smoothstep",
      label: e.label,
      className: EDGE_CLASS[e.kind],
      markerEnd: { type: MarkerType.ArrowClosed, color: MARKER_COLOR[e.kind] },
      focusable: false,
    }));
    return { nodes, edges };
  }, [model, selectedRole, direction, onSelectRole]);

  // Re-fit when the set of roles changes; layout of existing roles never moves.
  const roleKey = `${direction}:${model.nodes.map((n) => n.id).join("|")}`;

  if (model.nodes.length === 0) {
    return (
      <div className={cn("flex items-center justify-center p-4 text-sm text-muted-foreground", className)}>
        No roles yet. The graph appears once the team is known.
      </div>
    );
  }

  // Natural height: the lowest node's bottom edge, plus room for edge labels and padding.
  const naturalHeight = Math.max(...model.nodes.map((n) => n.y)) - Math.min(...model.nodes.map((n) => n.y))
    + NODE_HEIGHT + 72;
  return (
    <div
      className={cn("w-full", fitHeight ? "" : "h-full min-h-[10rem]", className)}
      style={fitHeight ? { height: naturalHeight } : undefined}
      data-testid="pipeline-graph"
    >
      <ReactFlow
        key={roleKey}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
        minZoom={0.3}
        maxZoom={1.5}
        nodesDraggable={false}
        panOnScroll={false}
        zoomOnScroll={false}
        preventScrolling={false}
        nodesConnectable={false}
        elementsSelectable={false}
        // Nodes only take pointer events when the graph has a click handler. The button
        // inside each node is the keyboard path; both open the same details.
        onNodeClick={(_, node) => onSelectRole?.(node.id)}
        proOptions={{ hideAttribution: true }}
        aria-label="Pipeline graph"
      />
    </div>
  );
}

/**
 * The six edge anchors per node. LR: dependencies enter left / leave right, review edges use
 * the bottom, blocked-on arrows the top. TB rotates that: dependencies top → bottom, review on
 * the right, blocked-on on the left, so edges of one kind never overlap another's.
 */
function handleGeometry(direction: GraphDirection) {
  const size = 8;
  const w = NODE_WIDTH;
  const h = NODE_HEIGHT;
  const at = (side: Position, frac: number) => {
    switch (side) {
      case Position.Left: return { x: -size / 2, y: h * frac - size / 2 };
      case Position.Right: return { x: w - size / 2, y: h * frac - size / 2 };
      case Position.Top: return { x: w * frac - size / 2, y: -size / 2 };
      default: return { x: w * frac - size / 2, y: h - size / 2 };
    }
  };
  const lr = direction === "LR";
  const spec: [string, "source" | "target", Position, number][] = [
    ["in", "target", lr ? Position.Left : Position.Top, 0.5],
    ["out", "source", lr ? Position.Right : Position.Bottom, 0.5],
    ["review-out", "source", lr ? Position.Bottom : Position.Right, 1 / 3],
    ["review-in", "target", lr ? Position.Bottom : Position.Right, 2 / 3],
    ["blocked-out", "source", lr ? Position.Top : Position.Left, 1 / 3],
    ["blocked-in", "target", lr ? Position.Top : Position.Left, 2 / 3],
  ];
  return spec.map(([id, type, position, frac]) => ({
    id, type, position, ...at(position, frac), width: size, height: size,
  }));
}

// Where React Flow's <Handle> sits along its side (it centres by default).
const HANDLE_OFFSET: Record<string, string> = {
  "review-out": "data-[handlepos=bottom]:!left-1/3 data-[handlepos=right]:!top-1/3",
  "review-in": "data-[handlepos=bottom]:!left-2/3 data-[handlepos=right]:!top-2/3",
  "blocked-out": "data-[handlepos=top]:!left-1/3 data-[handlepos=left]:!top-1/3",
  "blocked-in": "data-[handlepos=top]:!left-2/3 data-[handlepos=left]:!top-2/3",
};
