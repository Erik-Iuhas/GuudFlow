import { useState } from "react";
import type { AgentConfig, ModelTier } from "../types";
import { avatarFor } from "../lib/avatar";
import { ToolsMultiSelect } from "./ToolsMultiSelect";
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
        {header}

        <label className="small">Description</label>
        <input
          value={agent.description ?? ""}
          onChange={(e) => onChange?.({ description: e.target.value })}
        />

        <label className="small" style={{ marginTop: 8 }}>System prompt</label>
        <textarea
          rows={3}
          value={agent.system_prompt ?? ""}
          onChange={(e) => onChange?.({ system_prompt: e.target.value })}
        />

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

        <label className="small" style={{ marginTop: 8 }}>Tools</label>
        <div style={{ position: "relative" }}>
          <ToolsMultiSelect
            selected={agent.declared_local_tools ?? []}
            onChange={(next) => onChange?.({ declared_local_tools: next })}
            archetypeRoleType={agent.agent_class_role_type ?? null}
            projectRbacScopes={projectRbacScopes}
          />
        </div>

        <label className="small" style={{ marginTop: 8 }}>Depends on</label>
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          {allRoles
            .filter((r) => r !== agent.role)
            .map((r) => {
              const checked = (agent.depends_on ?? []).includes(r);
              return (
                <label key={r} className="small" style={{ marginBottom: 0 }}>
                  <input
                    type="checkbox"
                    checked={checked}
                    style={{ width: "auto", marginRight: 4 }}
                    onChange={() => {
                      const next = checked
                        ? (agent.depends_on ?? []).filter((x) => x !== r)
                        : [...(agent.depends_on ?? []), r];
                      onChange?.({ depends_on: next });
                    }}
                  />
                  {r}
                </label>
              );
            })}
        </div>

        <div className="row" style={{ marginTop: 8 }}>
          <label className="small" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={agent.is_reviewer ?? false}
              style={{ width: "auto", marginRight: 4 }}
              onChange={(e) => onChange?.({ is_reviewer: e.target.checked })}
            />
            Reviewer (merges branches on APPROVED)
          </label>
        </div>

        <div className="row" style={{ marginTop: 8, gap: 12 }}>
          <div style={{ flex: 1 }}>
            <label className="small">Max tool iterations</label>
            <input
              type="number"
              min={1}
              max={50}
              value={agent.max_tool_iterations ?? 6}
              onChange={(e) =>
                onChange?.({
                  max_tool_iterations: parseInt(e.target.value || "6", 10),
                })
              }
            />
          </div>
        </div>

        {onOpenSettings && (
          <button
            className="settings-gear"
            onClick={onOpenSettings}
            title="Agent settings"
            style={{ marginTop: 8 }}
          >
            ⚙ Settings
          </button>
        )}
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
          <div className="chip-row" style={{ marginTop: 6, flexWrap: "wrap", gap: 4 }}>
            {(agent.declared_local_tools ?? []).map((t) => (
              <span key={t} className="tag small">{t}</span>
            ))}
          </div>
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
