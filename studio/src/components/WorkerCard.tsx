import { useState } from "react";
import type { AgentConfig, ModelTier } from "../types";
import { avatarFor } from "../lib/avatar";
import { ActiveTaskChip } from "./ActiveTaskChip";

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

function Avatar({ seed }: { seed: string }) {
  const av = avatarFor(seed);
  return (
    <div
      className="worker-avatar"
      style={{ background: av.gradient }}
      aria-hidden
    >
      {av.initials}
    </div>
  );
}

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
    <div className="worker-card-header">
      <Avatar seed={avatarSeed} />
      <div className="worker-card-identity">
        <span className="worker-card-name">{agent.role}</span>
        <span className={`tag ${agent.model_tier ?? "solid"}`}>
          {agent.model_tier ?? "solid"}
        </span>
        {agent.is_reviewer && <span className="tag">reviewer</span>}
      </div>
      {agent.persona_id && (onOpenPersona || mode === "live" || mode === "roster") && (
        <button
          className="persona-badge"
          onClick={() => onOpenPersona?.(agent.persona_id!)}
          title={`View persona ${agent.persona_id}`}
        >
          {agent.persona_id.slice(0, 8)}
        </button>
      )}
    </div>
  );

  // ── config mode ──────────────────────────────────────────────────────────
  if (mode === "config") {
    return (
      <div className="card agent-card worker-card">
        {onRemove && (
          <button className="remove" onClick={onRemove} title="remove">×</button>
        )}
        <div className="worker-card-header" style={{ position: "relative" }}>
          <Avatar seed={avatarSeed} />
          <div className="worker-card-identity">
            <span className="worker-card-name">{agent.role}</span>
            <div className="row" style={{ gap: 4, marginTop: 2 }}>
              <span className={`tag ${agent.model_tier ?? "solid"}`}>
                {agent.model_tier ?? "solid"}
              </span>
              {agent.is_reviewer && <span className="tag">reviewer</span>}
            </div>
          </div>
          {onOpenSettings && (
            <button
              className="settings-gear"
              onClick={onOpenSettings}
              title="Open agent settings"
              style={{ marginLeft: "auto" }}
            >
              ⚙
            </button>
          )}
        </div>

        {/* Inline model-tier picker — most frequently changed field stays on card */}
        <div style={{ marginTop: 8 }}>
          <label className="small">Model</label>
          <select
            value={agent.model_tier ?? "solid"}
            onChange={(e) => onChange?.({ model_tier: e.target.value as ModelTier })}
            style={{ width: "100%" }}
          >
            {MODEL_OPTIONS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label} ({m.sublabel})
              </option>
            ))}
          </select>
        </div>
      </div>
    );
  }

  // ── preview mode ─────────────────────────────────────────────────────────
  if (mode === "preview") {
    return (
      <div className="card agent-card worker-card worker-card--preview">
        {header}
        <div className="muted small">{agent.description}</div>
        {(agent.declared_local_tools ?? []).length > 0 && (
          <div className="muted small" style={{ marginTop: 4 }}>
            tools:{" "}
            <span>{(agent.declared_local_tools ?? []).join(", ")}</span>
          </div>
        )}
        {(agent.depends_on ?? []).length > 0 && (
          <div className="muted small">
            depends on: {(agent.depends_on ?? []).join(", ")}
          </div>
        )}
      </div>
    );
  }

  // ── live mode ─────────────────────────────────────────────────────────────
  if (mode === "live") {
    return (
      <div
        className="card agent-card worker-card worker-card--live"
        onClick={() => onJumpToAgent?.(agent.role)}
        style={{ cursor: onJumpToAgent ? "pointer" : undefined }}
      >
        {header}
        {runId && (
          <div style={{ marginTop: 6 }}>
            <ActiveTaskChip runId={runId} role={agent.role} />
          </div>
        )}
        {(agent.declared_local_tools ?? []).length > 0 && (
          <details style={{ marginTop: 6 }}>
            <summary className="small muted" style={{ cursor: "pointer", userSelect: "none" }}>
              {agent.declared_local_tools!.length} tool{agent.declared_local_tools!.length !== 1 ? "s" : ""}
            </summary>
            <div className="chip-row" style={{ marginTop: 4, flexWrap: "wrap", gap: 4 }}>
              {(agent.declared_local_tools ?? []).map((t) => (
                <span key={t} className="tag small">{t}</span>
              ))}
            </div>
          </details>
        )}
        {onOpenSettings && (
          <button
            className="settings-gear"
            onClick={(e) => { e.stopPropagation(); onOpenSettings(); }}
            title="Agent settings"
          >
            ⚙
          </button>
        )}
      </div>
    );
  }

  // ── roster mode ───────────────────────────────────────────────────────────
  return (
    <div
      className="card agent-card worker-card worker-card--roster"
      onClick={() => agent.persona_id && onOpenPersona?.(agent.persona_id)}
      style={{ cursor: onOpenPersona ? "pointer" : undefined }}
    >
      {header}
    </div>
  );
}
