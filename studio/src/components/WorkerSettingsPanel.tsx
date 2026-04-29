import { useEffect, useRef, useState } from "react";
import type { AgentConfig, ModelTier } from "../types";
import { ToolsMultiSelect } from "./ToolsMultiSelect";

interface BaselineTool {
  scope: string;
  source: "baseline" | "archetype" | "project";
}

interface Props {
  agent: AgentConfig;
  allRoles?: string[];
  projectRbacScopes?: string[];
  onClose: () => void;
  onChange: (partial: Partial<AgentConfig>) => void;
  onSave: (partial: Partial<AgentConfig>) => Promise<void>;
}

function useEscapeClose(onClose: () => void) {
  useEffect(() => {
    function handle(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [onClose]);
}

export function WorkerSettingsPanel({
  agent,
  allRoles = [],
  projectRbacScopes = [],
  onClose,
  onChange,
  onSave,
}: Props) {
  useEscapeClose(onClose);

  // Local draft for optimistic edits
  const [draft, setDraft] = useState<Partial<AgentConfig>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  function merged(): AgentConfig {
    return { ...agent, ...draft };
  }

  async function handleChange(partial: Partial<AgentConfig>) {
    const next = { ...draft, ...partial };
    setDraft(next);
    // Optimistic: notify parent immediately, fire save in background
    onChange(partial);
    setSaveError(null);
    try {
      await onSave(partial);
    } catch (e) {
      setSaveError(String(e));
      // Revert
      setDraft((d) => {
        const reverted = { ...d };
        for (const k of Object.keys(partial) as (keyof AgentConfig)[]) {
          delete reverted[k];
        }
        return reverted;
      });
      // Tell parent to revert too
      const revert: Partial<AgentConfig> = {};
      for (const k of Object.keys(partial) as (keyof AgentConfig)[]) {
        (revert as Record<string, unknown>)[k] = agent[k];
      }
      onChange(revert);
    }
  }

  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === backdropRef.current) onClose();
  }

  const current = merged();

  // Build effective RBAC list for display
  const [baselineScopes, setBaselineScopes] = useState<string[]>([]);
  useEffect(() => {
    import("../api").then(({ toolsApi }) =>
      toolsApi.list().then((tools) => {
        setBaselineScopes(tools.filter((t) => t.baseline).map((t) => t.scope));
      }),
    ).catch(() => {});
  }, []);

  const archetypeScopes: string[] = agent.agent_class_role_type
    ? [] // populated via ToolsMultiSelect's own fetch; shown there
    : [];

  const effectiveScopes: { scope: string; source: string }[] = [
    ...baselineScopes.map((s) => ({ scope: s, source: "baseline" })),
    ...projectRbacScopes.map((s) => ({ scope: s, source: "project" })),
  ];

  return (
    <div
      ref={backdropRef}
      className="slide-over-backdrop"
      onClick={handleBackdropClick}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,.3)",
        zIndex: 200,
        display: "flex",
        justifyContent: "flex-end",
      }}
    >
      <div
        className="slide-over-panel"
        style={{
          width: 480,
          maxWidth: "100vw",
          background: "var(--bg, #fff)",
          height: "100%",
          overflowY: "auto",
          padding: 24,
          boxShadow: "-4px 0 20px rgba(0,0,0,.15)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 16 }}>
          <h2 style={{ margin: 0 }}>Agent settings — {current.role}</h2>
          <button onClick={onClose} aria-label="Close settings">×</button>
        </div>

        {saveError && (
          <div className="error" style={{ marginBottom: 12 }}>{saveError}</div>
        )}

        {/* ── Identity (read-only) ── */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>
            Identity
          </h3>
          <div><strong>Role:</strong> {current.role}</div>
          {current.persona_id && (
            <div className="muted small">Persona: {current.persona_id}</div>
          )}
          {current.agent_class_role_type && (
            <div className="muted small">Archetype: {current.agent_class_role_type}</div>
          )}
        </section>

        {/* ── Model ── */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>
            Model
          </h3>
          <label className="small">Tier</label>
          <select
            value={current.model_tier ?? "solid"}
            onChange={(e) => handleChange({ model_tier: e.target.value as ModelTier })}
            style={{ width: "100%" }}
          >
            <option value="heavy">Heavy</option>
            <option value="solid">Solid</option>
            <option value="fast">Fast</option>
          </select>
        </section>

        {/* ── Behaviour ── */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>
            Behaviour
          </h3>
          <label className="small">Max tool iterations</label>
          <input
            type="number"
            min={1}
            max={50}
            value={current.max_tool_iterations ?? 6}
            onChange={(e) =>
              handleChange({ max_tool_iterations: parseInt(e.target.value || "6", 10) })
            }
          />
          <label className="small" style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={current.is_reviewer ?? false}
              style={{ width: "auto" }}
              onChange={(e) => handleChange({ is_reviewer: e.target.checked })}
            />
            Reviewer (merges branches on APPROVED)
          </label>
        </section>

        {/* ── Tools ── */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>
            Tools
          </h3>
          <div style={{ position: "relative" }}>
            <ToolsMultiSelect
              selected={current.declared_local_tools ?? []}
              onChange={(next) => handleChange({ declared_local_tools: next })}
              archetypeRoleType={current.agent_class_role_type ?? null}
              projectRbacScopes={projectRbacScopes}
            />
          </div>
        </section>

        {/* ── Dependencies ── */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>
            Dependencies
          </h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {allRoles
              .filter((r) => r !== current.role)
              .map((r) => {
                const checked = (current.depends_on ?? []).includes(r);
                return (
                  <label key={r} className="small" style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      style={{ width: "auto" }}
                      onChange={() => {
                        const next = checked
                          ? (current.depends_on ?? []).filter((x) => x !== r)
                          : [...(current.depends_on ?? []), r];
                        handleChange({ depends_on: next });
                      }}
                    />
                    {r}
                  </label>
                );
              })}
          </div>
        </section>

        {/* ── RBAC (read-only) ── */}
        <section>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>
            Effective RBAC scopes
          </h3>
          <ul className="rbac-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {effectiveScopes.map(({ scope, source }) => (
              <li key={scope} className="small" style={{ marginBottom: 2 }}>
                <code>{scope}</code>
                <span className="muted" style={{ marginLeft: 6 }}>from {source}</span>
              </li>
            ))}
            {effectiveScopes.length === 0 && (
              <li className="muted small">No extra scopes (baseline only)</li>
            )}
          </ul>
        </section>
      </div>
    </div>
  );
}
