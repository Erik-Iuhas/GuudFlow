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
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  STATE_LABEL,
  buildGraph,
  type EdgeKind,
  type RoleSpec,
} from "./model";

type RoleNodeData = {
  role: string;
  state: AgentState;
  pulsing: boolean;
  detached: boolean;
  card?: string;
  selected: boolean;
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

function RoleNode({ data }: NodeProps<RoleNodeType>) {
  return (
    <div
      className={cn(
        "flex h-full w-full flex-col justify-center rounded-md border-2 px-3 py-1.5 text-foreground",
        STATE_BOX[data.state],
        data.detached && "border-dashed",
        data.pulsing && "motion-safe:animate-pulse",
        data.selected && "ring-2 ring-ring",
      )}
      data-testid={`graph-node-${data.role}`}
      data-state={data.state}
    >
      <Handle id="in" type="target" position={Position.Left} className="!h-2 !w-2 !border-border !bg-muted-foreground" />
      <Handle id="out" type="source" position={Position.Right} className="!h-2 !w-2 !border-border !bg-muted-foreground" />
      <Handle id="review-out" type="source" position={Position.Bottom} className="!left-1/3 !h-2 !w-2 !border-border !bg-status-waiting" />
      <Handle id="review-in" type="target" position={Position.Bottom} className="!left-2/3 !h-2 !w-2 !border-border !bg-status-waiting" />
      <Handle id="blocked-out" type="source" position={Position.Top} className="!left-1/3 !h-2 !w-2 !border-border !bg-status-blocked" />
      <Handle id="blocked-in" type="target" position={Position.Top} className="!left-2/3 !h-2 !w-2 !border-border !bg-status-blocked" />
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-semibold">{data.role}</span>
        <span className={cn("shrink-0 text-xs font-medium", STATE_TEXT[data.state])}>
          <span aria-hidden>{STATE_MARK[data.state]} </span>
          {STATE_LABEL[data.state]}
        </span>
      </div>
      <div className="truncate text-xs text-muted-foreground">
        {data.detached ? "overseer (detached)" : data.card ? `card ${data.card}` : "no active card"}
      </div>
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
}

/**
 * The live pipeline (React Flow): role nodes coloured by agent state (with a text label),
 * solid depends_on edges, review edges (reviewer -> author) carrying the rework round,
 * dashed blocked-on arrows, and the overseer as a detached node that pulses while it runs.
 */
export function PipelineGraph({ specs, selectedRole, onSelectRole, className }: PipelineGraphProps) {
  const agents = useRunStore((s) => s.agents);
  const reviews = useRunStore((s) => s.reviews);
  const blockedOn = useRunStore((s) => s.blockedOn);

  const model = useMemo(() => buildGraph(specs, agents, reviews, blockedOn), [specs, agents, reviews, blockedOn]);

  const { nodes, edges } = useMemo(() => {
    const nodes: RoleNodeType[] = model.nodes.map((n) => ({
      id: n.id,
      type: "role",
      // React Flow requires an explicit position per node; it is computed in model.ts.
      position: { x: n.x, y: n.y },
      initialWidth: NODE_WIDTH,
      initialHeight: NODE_HEIGHT,
      // Handle geometry up front so edges draw on the first paint (and under jsdom).
      handles: handleGeometry(),
      ariaLabel: `${n.role}: ${STATE_LABEL[n.state]}`,
      data: {
        role: n.role, state: n.state, pulsing: n.pulsing, detached: n.detached, card: n.card,
        selected: selectedRole === n.role,
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
  }, [model, selectedRole]);

  // Re-fit when the set of roles changes; layout of existing roles never moves.
  const roleKey = model.nodes.map((n) => n.id).join("|");

  if (model.nodes.length === 0) {
    return (
      <div className={cn("flex items-center justify-center p-4 text-sm text-muted-foreground", className)}>
        No roles yet. The graph appears once the team is known.
      </div>
    );
  }

  return (
    <div className={cn("h-full min-h-[10rem] w-full", className)} data-testid="pipeline-graph">
      <ReactFlow
        key={roleKey}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.3}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        onNodeClick={(_, node) => onSelectRole?.(node.id)}
        aria-label="Pipeline graph"
      />
    </div>
  );
}

/** Positions of the six handles in a node's box, matching the classes in `RoleNode`. */
function handleGeometry() {
  const size = 8;
  const mid = NODE_HEIGHT / 2 - size / 2;
  return [
    { id: "in", type: "target" as const, position: Position.Left, x: -size / 2, y: mid, width: size, height: size },
    { id: "out", type: "source" as const, position: Position.Right, x: NODE_WIDTH - size / 2, y: mid, width: size, height: size },
    { id: "review-out", type: "source" as const, position: Position.Bottom, x: NODE_WIDTH / 3 - size / 2, y: NODE_HEIGHT - size / 2, width: size, height: size },
    { id: "review-in", type: "target" as const, position: Position.Bottom, x: (NODE_WIDTH * 2) / 3 - size / 2, y: NODE_HEIGHT - size / 2, width: size, height: size },
    { id: "blocked-out", type: "source" as const, position: Position.Top, x: NODE_WIDTH / 3 - size / 2, y: -size / 2, width: size, height: size },
    { id: "blocked-in", type: "target" as const, position: Position.Top, x: (NODE_WIDTH * 2) / 3 - size / 2, y: -size / 2, width: size, height: size },
  ];
}
