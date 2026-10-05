// Pure layout for the team graph: layers by dependency depth, review edges back to authors.

import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { HarnessAgentConfig } from "../../lib/capabilities";

export interface RoleNodeData extends Record<string, unknown> {
  role: string;
  roleType: string;
  tier: string;
  description: string;
  capabilities: string[];
}

export type RoleNode = Node<RoleNodeData, "role">;

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 112;
const COL_GAP = 90;
const ROW_GAP = 28;

/** Longest dependency chain ending at each role; cycles and unknown deps are ignored. */
export function dependencyDepths(agents: HarnessAgentConfig[]): Record<string, number> {
  const byRole = new Map(agents.map((a) => [a.role, a]));
  const depth: Record<string, number> = {};
  const visiting = new Set<string>();
  function visit(role: string): number {
    if (role in depth) return depth[role];
    if (visiting.has(role)) return 0;
    visiting.add(role);
    let d = 0;
    for (const dep of byRole.get(role)?.depends_on ?? []) {
      if (byRole.has(dep)) d = Math.max(d, visit(dep) + 1);
    }
    visiting.delete(role);
    depth[role] = d;
    return d;
  }
  for (const a of agents) visit(a.role);
  return depth;
}

/**
 * Nodes (columns by depth) and edges: solid dependency edges author -> dependent, and
 * dashed review edges reviewer -> author labelled with the max review rounds.
 */
export function layoutTeam(
  agents: HarnessAgentConfig[],
  summaries: Record<string, { roleType: string; capabilities: string[] }> = {},
): { nodes: RoleNode[]; edges: Edge[] } {
  const depth = dependencyDepths(agents);
  const rowInColumn: Record<number, number> = {};
  const roles = new Set(agents.map((a) => a.role));

  const nodes: RoleNode[] = agents.map((a) => {
    const d = depth[a.role] ?? 0;
    const row = (rowInColumn[d] = (rowInColumn[d] ?? -1) + 1);
    const summary = summaries[a.role];
    return {
      id: a.role,
      type: "role",
      // React Flow requires explicit coordinates; this is layout data, not styling.
      position: { x: d * (NODE_WIDTH + COL_GAP), y: row * (NODE_HEIGHT + ROW_GAP) },
      // Declaring size and handles lets edges render before the DOM is measured (SSR / tests).
      initialWidth: NODE_WIDTH,
      initialHeight: NODE_HEIGHT,
      handles: [
        { id: "in", type: "target", position: "left" as never, x: 0, y: NODE_HEIGHT / 2, width: 1, height: 1 },
        { id: "out", type: "source", position: "right" as never, x: NODE_WIDTH, y: NODE_HEIGHT / 2, width: 1, height: 1 },
        { id: "review-in", type: "target", position: "bottom" as never, x: NODE_WIDTH / 3, y: NODE_HEIGHT, width: 1, height: 1 },
        { id: "review-out", type: "source", position: "bottom" as never, x: (2 * NODE_WIDTH) / 3, y: NODE_HEIGHT, width: 1, height: 1 },
      ],
      data: {
        role: a.role,
        roleType: summary?.roleType || a.role_type || a.agent_class_role_type || "agent",
        tier: a.model_tier ?? "solid",
        description: a.description ?? "",
        capabilities: summary?.capabilities ?? [],
      },
    };
  });

  const edges: Edge[] = [];
  for (const a of agents) {
    for (const dep of a.depends_on ?? []) {
      if (!roles.has(dep)) continue;
      edges.push({ id: `dep:${dep}->${a.role}`, source: dep, target: a.role, sourceHandle: "out", targetHandle: "in",
        className: "team-edge-dep",
        markerEnd: { type: MarkerType.ArrowClosed, color: "hsl(var(--muted-foreground))" } });
    }
    for (const author of a.review_of ?? []) {
      if (!roles.has(author)) continue;
      const rounds = a.max_review_rounds ?? 2;
      edges.push({
        id: `review:${a.role}->${author}`,
        source: a.role,
        target: author,
        // Bottom handles route the loop under the nodes, clear of the dependency edge.
        sourceHandle: "review-out",
        targetHandle: "review-in",
        type: "smoothstep",
        label: `review, max ${rounds} ${rounds === 1 ? "round" : "rounds"}`,
        className: "team-edge-review",
        markerEnd: { type: MarkerType.ArrowClosed, color: "hsl(var(--status-waiting))" },
        animated: true,
      });
    }
  }
  return { nodes, edges };
}
