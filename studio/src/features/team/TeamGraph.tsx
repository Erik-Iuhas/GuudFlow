import { useMemo, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type NodeProps,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { CapabilitySwitches } from "../../components/CapabilitySwitches";
import { CostStrip } from "../../components/CostStrip";
import { Badge } from "../../components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "../../components/ui/sheet";
import {
  capabilitySummary,
  type CapabilitiesMeta,
  type CapabilityBlock,
  type HarnessAgentConfig,
  type RoleCapState,
} from "../../lib/capabilities";
import { tierClass } from "../../lib/cost";
import { cn } from "../../lib/utils";
import type { ModelTier } from "../../types";
import { layoutTeam, type RoleNode } from "./layout";

function RoleNodeView({ data, selected }: NodeProps<RoleNode>) {
  return (
    <div
      data-testid={`team-node-${data.role}`}
      className={cn(
        "w-[220px] rounded-lg border bg-card p-3 text-card-foreground shadow-sm",
        selected ? "border-primary ring-2 ring-ring" : "border-border",
      )}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-sm font-semibold">{data.role}</span>
        <Badge variant="secondary" className="font-mono">{data.roleType}</Badge>
      </div>
      <div className={cn("mt-1 text-xs", tierClass(data.tier as ModelTier))}>tier: {data.tier}</div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {data.capabilities.length === 0 ? (
          <span className="text-xs text-muted-foreground">default capabilities</span>
        ) : (
          data.capabilities.map((c) => (
            <Badge key={c} variant="outline" className="font-mono text-[10px]">{c}</Badge>
          ))
        )}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const NODE_TYPES: NodeTypes = { role: RoleNodeView };

interface TeamGraphProps {
  projectId: string;
  goal: string;
  agents: HarnessAgentConfig[];
  /** Resolved capabilities per role; empty on the legacy harness. */
  roleCaps: Record<string, RoleCapState>;
  capMeta: CapabilitiesMeta | null;
  onCapabilityChange: (agent: HarnessAgentConfig, next: CapabilityBlock) => void;
  onTierChange: (role: string, tier: ModelTier) => void;
}

/**
 * The proposed team as a graph (plan 12.3): role nodes, dependency edges and review
 * edges. Selecting a node opens a side sheet with that role's tier and capability
 * switches; the cost estimate under the graph follows every change.
 */
export function TeamGraph({
  projectId,
  goal,
  agents,
  roleCaps,
  capMeta,
  onCapabilityChange,
  onTierChange,
}: TeamGraphProps) {
  const [selected, setSelected] = useState<string | null>(null);

  const { nodes, edges } = useMemo(() => {
    const summaries: Record<string, { roleType: string; capabilities: string[] }> = {};
    for (const [role, rc] of Object.entries(roleCaps)) {
      summaries[role] = { roleType: rc.roleType, capabilities: capabilitySummary(rc.capabilities) };
    }
    return layoutTeam(agents, summaries);
  }, [agents, roleCaps]);

  const capsByRole = useMemo(() => {
    const out: Record<string, CapabilityBlock> = {};
    for (const [role, rc] of Object.entries(roleCaps)) out[role] = rc.capabilities;
    return out;
  }, [roleCaps]);

  const agent = agents.find((a) => a.role === selected) ?? null;
  const rc = agent ? roleCaps[agent.role] : undefined;

  return (
    <div data-testid="team-graph" className="flex flex-col gap-3">
      <div className="h-[380px] overflow-hidden rounded-lg border border-border bg-background">
        <ReactFlow
          nodes={nodes.map((n) => ({ ...n, selected: n.id === selected }))}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          fitView
          minZoom={0.3}
          onNodeClick={(_, node) => setSelected(node.id)}
          onPaneClick={() => setSelected(null)}
          proOptions={{ hideAttribution: true }}
          aria-label="Proposed team graph"
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <p className="text-xs text-muted-foreground">
        Solid lines: depends on. Dashed lines: reviewer sends work back to the author.
        Select a role to change its model tier and what it may do.
      </p>

      <CostStrip
        project={{ project_id: projectId, goal, agents, max_iterations: 5 }}
        capsByRole={capsByRole}
      />

      <Sheet open={!!agent} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent side="right" className="w-[420px] max-w-full overflow-y-auto">
          {agent && (
            <>
              <SheetHeader>
                <SheetTitle className="font-mono">{agent.role}</SheetTitle>
                <SheetDescription>{agent.description || "No description."}</SheetDescription>
              </SheetHeader>
              <div className="mt-4 flex flex-col gap-4">
                <label className="flex items-center gap-2 text-xs">
                  Model tier
                  <select
                    aria-label={`${agent.role} model tier`}
                    value={agent.model_tier ?? "solid"}
                    onChange={(e) => onTierChange(agent.role, e.target.value as ModelTier)}
                  >
                    <option value="heavy">heavy</option>
                    <option value="solid">solid</option>
                    <option value="fast">fast</option>
                  </select>
                </label>
                {capMeta && rc ? (
                  <CapabilitySwitches
                    role={agent.role}
                    roleType={rc.roleType}
                    value={rc.capabilities}
                    onChange={(next) => onCapabilityChange(agent, next)}
                    levels={capMeta.levels}
                    defaults={capMeta.defaults[rc.roleType]}
                    visionAvailable={rc.visionAvailable}
                    production={capMeta.production}
                    hostDevWarning={capMeta.host_dev_warning}
                    error={rc.error}
                  />
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Choose the opencode harness to set what this role may do.
                  </p>
                )}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
