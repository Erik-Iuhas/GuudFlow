import { useState } from "react";
import type { AgentConfig, ModelTier } from "../types";
import { fieldCls, labelCls, tierVariant } from "../lib/field";
import { cn } from "../lib/utils";
import { ActiveTaskChip } from "./ActiveTaskChip";
import { AvatarDot } from "./AvatarDot";
import { Badge } from "./ui/badge";
import { Card } from "./ui/card";

export type WorkerCardMode = "config" | "preview" | "live" | "roster";

interface WorkerCardProps {
  agent: AgentConfig;
  allRoles?: string[];
  mode: WorkerCardMode;
  // config-only
  onChange?: (partial: Partial<AgentConfig>) => void;
  onRemove?: () => void;
  onOpenSettings?: () => void;
  // config: RBAC context for ToolsMultiSelect
  projectRbacScopes?: string[];
  // live-only
  runId?: string;
  // roster + live
  onOpenPersona?: (personaId: string) => void;
  // live: jump to agent section in BoardFeed
  onJumpToAgent?: (role: string) => void;
}

const MODEL_OPTIONS: { value: ModelTier; label: string; sublabel: string }[] = [
  { value: "heavy", label: "Heavy", sublabel: "claude-opus-4-7" },
  { value: "solid", label: "Solid", sublabel: "claude-sonnet-4-6" },
  { value: "fast",  label: "Fast",  sublabel: "claude-haiku-4-5" },
];

export function WorkerCard({
  agent,
  allRoles = [],
  mode,
  onChange,
  onRemove,
  onOpenSettings,
  projectRbacScopes = [],
  runId,
  onOpenPersona,
  onJumpToAgent,
}: WorkerCardProps) {
  const avatarSeed = agent.persona_id || agent.role;

  const header = (
    <div className="flex items-center gap-2.5">
      <AvatarDot seed={avatarSeed} />
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-sm">
          <span className="font-semibold">{agent.name || agent.role}</span>
          {agent.name && <span className="text-xs text-muted-foreground"> · {agent.role}</span>}
        </span>
        <Badge variant={tierVariant(agent.model_tier)}>
          {agent.model_tier ?? "solid"}
        </Badge>
        {agent.is_reviewer && <Badge variant="secondary">reviewer</Badge>}
      </div>
      {agent.persona_id && (onOpenPersona || mode === "live" || mode === "roster") && (
        <button
          type="button"
          className="ml-auto cursor-pointer rounded-md border border-border bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground hover:bg-accent"
          onClick={() => onOpenPersona?.(agent.persona_id!)}
          title={`View persona ${agent.persona_id}`}
        >
          {agent.persona_id.slice(0, 8)}
        </button>
      )}
    </div>
  );

  const gearCls =
    "cursor-pointer rounded-md border-0 bg-transparent p-1 text-base text-muted-foreground hover:bg-accent hover:text-accent-foreground";

  // ── config mode ──────────────────────────────────────────────────────────
  if (mode === "config") {
    return (
      <Card className="relative mb-3 p-4">
        {onRemove && (
          <button
            type="button"
            className="absolute right-2 top-2 cursor-pointer rounded-md border-0 bg-transparent px-1.5 py-0.5 text-base text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            onClick={onRemove}
            title="remove"
          >
            ×
          </button>
        )}
        <div className="relative flex items-center gap-2.5">
          <AvatarDot seed={avatarSeed} />
          <div className="flex flex-col gap-0.5">
            <span className="text-sm">
          <span className="font-semibold">{agent.name || agent.role}</span>
          {agent.name && <span className="text-xs text-muted-foreground"> · {agent.role}</span>}
        </span>
            <div className="mt-0.5 flex items-center gap-1">
              <Badge variant={tierVariant(agent.model_tier)}>
                {agent.model_tier ?? "solid"}
              </Badge>
              {agent.is_reviewer && <Badge variant="secondary">reviewer</Badge>}
            </div>
          </div>
          {onOpenSettings && (
            <button
              type="button"
              className={cn(gearCls, "ml-auto")}
              onClick={onOpenSettings}
              title="Open agent settings"
            >
              ⚙
            </button>
          )}
        </div>

        {/* Inline model-tier picker — most frequently changed field stays on card */}
        <div className="mt-2">
          <label className={labelCls}>Model</label>
          <select
            className={fieldCls}
            value={agent.model_tier ?? "solid"}
            onChange={(e) => onChange?.({ model_tier: e.target.value as ModelTier })}
          >
            {MODEL_OPTIONS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label} ({m.sublabel})
              </option>
            ))}
          </select>
        </div>
      </Card>
    );
  }

  // ── preview mode ─────────────────────────────────────────────────────────
  if (mode === "preview") {
    return (
      <Card className="mb-3 p-4">
        {header}
        <div className="mt-2 text-xs text-muted-foreground">{agent.description}</div>
        {(agent.declared_local_tools ?? []).length > 0 && (
          <div className="mt-1 text-xs text-muted-foreground">
            tools:{" "}
            <span>{(agent.declared_local_tools ?? []).join(", ")}</span>
          </div>
        )}
        {(agent.depends_on ?? []).length > 0 && (
          <div className="text-xs text-muted-foreground">
            depends on: {(agent.depends_on ?? []).join(", ")}
          </div>
        )}
      </Card>
    );
  }

  // ── live mode ─────────────────────────────────────────────────────────────
  if (mode === "live") {
    return (
      <Card
        className={cn("relative mb-3 p-4", onJumpToAgent && "cursor-pointer")}
        onClick={() => onJumpToAgent?.(agent.role)}
      >
        {header}
        {runId && (
          <div className="mt-1.5">
            <ActiveTaskChip runId={runId} role={agent.role} />
          </div>
        )}
        {(agent.declared_local_tools ?? []).length > 0 && (
          <details className="mt-1.5">
            <summary className="cursor-pointer select-none text-xs text-muted-foreground">
              {agent.declared_local_tools!.length} tool{agent.declared_local_tools!.length !== 1 ? "s" : ""}
            </summary>
            <div className="mt-1 flex flex-wrap gap-1">
              {(agent.declared_local_tools ?? []).map((t) => (
                <Badge key={t} variant="secondary">{t}</Badge>
              ))}
            </div>
          </details>
        )}
        {onOpenSettings && (
          <button
            type="button"
            className={cn(gearCls, "absolute right-2 top-2")}
            onClick={(e) => { e.stopPropagation(); onOpenSettings(); }}
            title="Agent settings"
          >
            ⚙
          </button>
        )}
      </Card>
    );
  }

  // ── roster mode ───────────────────────────────────────────────────────────
  return (
    <Card
      className={cn("worker-card--roster mb-3 p-4", onOpenPersona && "cursor-pointer")}
      onClick={() => agent.persona_id && onOpenPersona?.(agent.persona_id)}
    >
      {header}
    </Card>
  );
}
