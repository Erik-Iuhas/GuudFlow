import { useEffect, useRef, useState } from "react";
import type { AgentConfig, ModelTier } from "../types";
import type { ModelEntry } from "../api";

interface Props {
  agent: AgentConfig;
  allRoles?: string[];
  projectRbacScopes?: string[];
  onClose: () => void;
  onChange: (partial: Partial<AgentConfig>) => void;
  onSave: (partial: Partial<AgentConfig>) => Promise<void>;
  onRbacChange?: (scopesToAdd: string[], scopesToRemove: string[]) => void;
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

// ── Two-level model picker ────────────────────────────────────────────────────

const TIER_LABEL: Record<string, string> = {
  heavy: "Heavy",
  solid: "Solid",
  fast: "Fast",
};

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  groq: "Groq",
  deepseek: "DeepSeek",
  openrouter: "OpenRouter",
  ollama: "Ollama",
};

let _modelsCache: ModelEntry[] | null = null;
let _modelsPromise: Promise<ModelEntry[]> | null = null;

function fetchModels(): Promise<ModelEntry[]> {
  if (_modelsCache) return Promise.resolve(_modelsCache);
  if (!_modelsPromise) {
    _modelsPromise = import("../api")
      .then(({ modelsApi }) => modelsApi.list())
      .then((m) => {
        _modelsCache = m;
        return m;
      })
      .catch((e) => {
        _modelsPromise = null;
        throw e;
      });
  }
  return _modelsPromise;
}

function ModelPicker({
  value,
  onChange,
}: {
  value: ModelTier;
  onChange: (tier: ModelTier) => void;
}) {
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [provider, setProvider] = useState<string>("");

  useEffect(() => {
    fetchModels()
      .then((ms) => {
        setModels(ms);
        // Pick provider that has a model matching the current tier, default anthropic
        const match = ms.find((m) => m.tier === value);
        setProvider(match?.provider ?? "anthropic");
      })
      .catch(() => {});
  }, []);

  const providers = [...new Set(models.map((m) => m.provider))];
  const providerModels = models.filter((m) => m.provider === provider);

  function handleProviderChange(p: string) {
    setProvider(p);
    // Auto-select first model in new provider
    const first = models.find((m) => m.provider === p);
    if (first) onChange(first.tier as ModelTier);
  }

  function handleModelChange(modelId: string) {
    const m = models.find((m) => m.id === modelId);
    if (m) onChange(m.tier as ModelTier);
  }

  // Find the currently selected model in the active provider
  const currentModel = providerModels.find((m) => m.tier === value) ?? providerModels[0];

  if (models.length === 0) {
    // Fallback while loading or if API unavailable
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as ModelTier)}
        style={{ width: "100%" }}
      >
        <option value="heavy">Heavy (claude-opus-4-7)</option>
        <option value="solid">Solid (claude-sonnet-4-6)</option>
        <option value="fast">Fast (claude-haiku-4-5)</option>
      </select>
    );
  }

  return (
    <div style={{ display: "flex", gap: 8 }}>
      <select
        value={provider}
        onChange={(e) => handleProviderChange(e.target.value)}
        style={{ width: 140, flexShrink: 0 }}
        aria-label="Provider"
      >
        {providers.map((p) => (
          <option key={p} value={p}>
            {PROVIDER_LABEL[p] ?? p}
          </option>
        ))}
      </select>
      <select
        value={currentModel?.id ?? ""}
        onChange={(e) => handleModelChange(e.target.value)}
        style={{ flex: 1 }}
        aria-label="Model"
      >
        {providerModels.map((m) => (
          <option key={m.id} value={m.id}>
            {m.id} ({TIER_LABEL[m.tier] ?? m.tier})
          </option>
        ))}
      </select>
    </div>
  );
}

// ── Grouped tool picker ───────────────────────────────────────────────────────

interface ToolEntry {
  name: string;
  scope: string;
  description: string;
  baseline: boolean;
  granted_by_archetypes?: string[];
}

const NAMESPACE_LABELS: Record<string, string> = {
  fs: "File System",
  git: "Version Control",
  web: "Web",
  code: "Code Execution",
  desk: "Knowledge",
  mcp: "External MCP",
  task: "Task Management",
};

function toolNamespace(tool: ToolEntry): string {
  if (tool.scope.includes(":")) return tool.scope.split(":")[0];
  return tool.scope.split("-")[0];
}

function isToolEnabled(
  tool: ToolEntry,
  archetypeRoleType: string | null,
  projectRbacScopes: string[],
): boolean {
  if (tool.baseline) return true;
  if (archetypeRoleType && (tool.granted_by_archetypes ?? []).includes(archetypeRoleType)) return true;
  if (projectRbacScopes.includes(tool.scope)) return true;
  return false;
}

let _toolsCache: ToolEntry[] | null = null;
let _toolsPromise: Promise<ToolEntry[]> | null = null;

function fetchTools(): Promise<ToolEntry[]> {
  if (_toolsCache) return Promise.resolve(_toolsCache);
  if (!_toolsPromise) {
    _toolsPromise = import("../api")
      .then(({ toolsApi }) => toolsApi.list())
      .then((t) => {
        _toolsCache = t;
        return t;
      })
      .catch((e) => {
        _toolsPromise = null; // allow retry after failure
        throw e;
      });
  }
  return _toolsPromise;
}

function GroupedToolPicker({
  selected,
  onChange,
  onRbacChange,
  archetypeRoleType,
  projectRbacScopes,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
  onRbacChange?: (add: string[], remove: string[]) => void;
  archetypeRoleType: string | null;
  projectRbacScopes: string[];
}) {
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchTools()
      .then(setTools)
      .catch(() => setError(true));
  }, []);

  if (error) {
    return <p className="muted small">Could not load tools (check bearer token).</p>;
  }

  const groups = new Map<string, ToolEntry[]>();
  for (const t of tools) {
    const ns = toolNamespace(t);
    if (!groups.has(ns)) groups.set(ns, []);
    groups.get(ns)!.push(t);
  }

  const knownOrder = Object.keys(NAMESPACE_LABELS);
  const orderedKeys = [
    ...knownOrder.filter((k) => groups.has(k)),
    ...[...groups.keys()].filter((k) => !knownOrder.includes(k)).sort(),
  ];

  function toggleGroup(ns: string) {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(ns)) next.delete(ns);
      else next.add(ns);
      return next;
    });
  }

  function isOffArchetype(t: ToolEntry): boolean {
    if (t.baseline) return false;
    return !isToolEnabled(t, archetypeRoleType, projectRbacScopes);
  }

  function toggleTool(t: ToolEntry) {
    if (t.baseline) return;
    const scope = t.scope;
    if (selected.includes(scope)) {
      onChange(selected.filter((s) => s !== scope));
      if (isOffArchetype(t)) onRbacChange?.([], [scope]);
    } else {
      onChange([...selected, scope]);
      if (isOffArchetype(t)) onRbacChange?.([scope], []);
    }
  }

  function selectAll(ns: string) {
    const nsTools = groups.get(ns) ?? [];
    const nsScopes = nsTools.filter((t) => !t.baseline).map((t) => t.scope);
    const offArchetypeAdd = nsTools.filter((t) => !t.baseline && isOffArchetype(t) && !selected.includes(t.scope)).map((t) => t.scope);
    onChange([...new Set([...selected, ...nsScopes])]);
    if (offArchetypeAdd.length) onRbacChange?.(offArchetypeAdd, []);
  }

  function deselectAll(ns: string) {
    const nsTools = groups.get(ns) ?? [];
    const nsScopes = new Set(nsTools.filter((t) => !t.baseline).map((t) => t.scope));
    const offArchetypeRemove = nsTools.filter((t) => !t.baseline && isOffArchetype(t) && selected.includes(t.scope)).map((t) => t.scope);
    onChange(selected.filter((s) => !nsScopes.has(s)));
    if (offArchetypeRemove.length) onRbacChange?.([], offArchetypeRemove);
  }

  if (tools.length === 0) {
    return <p className="muted small">Loading tools…</p>;
  }

  return (
    <div>
      {orderedKeys.map((ns) => {
        const nsTools = groups.get(ns) ?? [];
        const nsSelected = nsTools.filter((t) => selected.includes(t.scope)).length;
        const nsNonBaseline = nsTools.filter((t) => !t.baseline);
        const isOpen = openGroups.has(ns);
        const allSelected = nsNonBaseline.length > 0 && nsNonBaseline.every((t) => selected.includes(t.scope));
        return (
          <div key={ns} style={{ marginBottom: 4, border: "1px solid #e2e8f0", borderRadius: 6, overflow: "hidden" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 10px",
                background: "#f8fafc",
                cursor: "pointer",
              }}
              onClick={() => toggleGroup(ns)}
            >
              <input
                type="checkbox"
                checked={allSelected}
                style={{ width: "auto" }}
                onChange={(e) => { e.stopPropagation(); allSelected ? deselectAll(ns) : selectAll(ns); }}
                onClick={(e) => e.stopPropagation()}
                title={allSelected ? "Deselect all" : "Select all enabled"}
              />
              <span style={{ fontWeight: 600, fontSize: 12, flex: 1 }}>
                {NAMESPACE_LABELS[ns] ?? ns}
              </span>
              <span className="muted small">{nsSelected}/{nsTools.length}</span>
              <span style={{ fontSize: 10, transform: isOpen ? "rotate(90deg)" : "none", transition: "transform .1s" }}>▶</span>
            </div>
            {isOpen && (
              <div style={{ padding: "4px 0" }}>
                {nsTools.map((t) => {
                  const offArchetype = isOffArchetype(t);
                  const isSelected = selected.includes(t.scope);
                  const archLabel = archetypeRoleType ?? "this role";
                  const title = t.baseline
                    ? "Baseline — always granted"
                    : offArchetype
                      ? `Unusual for a ${archLabel} — grants ${t.scope} to this role for this run.`
                      : t.description;
                  return (
                    <label
                      key={t.scope}
                      title={title}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "4px 10px 4px 28px",
                        cursor: t.baseline ? "not-allowed" : "pointer",
                        background: t.baseline
                          ? (isSelected ? "#eef2ff" : "transparent")
                          : offArchetype
                            ? "rgba(251,146,60,.12)"
                            : isSelected
                              ? "#eef2ff"
                              : "transparent",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={t.baseline || isSelected}
                        disabled={t.baseline}
                        style={{ width: "auto" }}
                        onChange={() => !t.baseline && toggleTool(t)}
                      />
                      <span style={{ fontFamily: "monospace", fontSize: 12 }}>{t.name}</span>
                      {t.baseline && <span className="tag small" style={{ fontSize: 10 }}>baseline</span>}
                      {offArchetype && <span className="tag small" style={{ fontSize: 10, background: "#fed7aa", color: "#92400e", border: "none" }}>off-archetype</span>}
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Main panel ───────────────────────────────────────────────────────────────

export function WorkerSettingsPanel({
  agent,
  allRoles = [],
  projectRbacScopes = [],
  onClose,
  onChange,
  onSave,
  onRbacChange,
}: Props) {
  useEscapeClose(onClose);

  const [draft, setDraft] = useState<Partial<AgentConfig>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const backdropRef = useRef<HTMLDivElement>(null);

  function merged(): AgentConfig {
    return { ...agent, ...draft };
  }

  async function handleChange(partial: Partial<AgentConfig>) {
    const next = { ...draft, ...partial };
    setDraft(next);
    onChange(partial);
    setSaveError(null);
    try {
      await onSave(partial);
    } catch (e) {
      setSaveError(String(e));
      setDraft((d) => {
        const reverted = { ...d };
        for (const k of Object.keys(partial) as (keyof AgentConfig)[]) delete reverted[k];
        return reverted;
      });
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

  const [baselineScopes, setBaselineScopes] = useState<string[]>([]);
  useEffect(() => {
    import("../api").then(({ toolsApi }) =>
      toolsApi.list().then((tools) => {
        setBaselineScopes(tools.filter((t) => t.baseline).map((t) => t.scope));
      }),
    ).catch(() => {});
  }, []);

  const effectiveScopes = [
    ...baselineScopes.map((s) => ({ scope: s, source: "baseline" })),
    ...projectRbacScopes.map((s) => ({ scope: s, source: "project" })),
  ];

  return (
    <div
      ref={backdropRef}
      className="slide-over-backdrop"
      onClick={handleBackdropClick}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.3)", zIndex: 200, display: "flex", justifyContent: "flex-end" }}
    >
      <div
        className="slide-over-panel"
        style={{ width: 480, maxWidth: "100vw", background: "var(--bg, #fff)", height: "100%", overflowY: "auto", padding: 24, boxShadow: "-4px 0 20px rgba(0,0,0,.15)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 16 }}>
          <h2 style={{ margin: 0 }}>Agent settings — {current.role}</h2>
          <button onClick={onClose} aria-label="Close settings">×</button>
        </div>

        {saveError && <div className="error" style={{ marginBottom: 12 }}>{saveError}</div>}

        {/* Identity */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>Identity</h3>
          <div><strong>Role:</strong> {current.role}</div>
          {current.persona_id && <div className="muted small" style={{ marginBottom: 8 }}>Persona: {current.persona_id}</div>}
          <label className="small">Archetype</label>
          <select
            value={current.agent_class_role_type ?? ""}
            onChange={(e) => void handleChange({ agent_class_role_type: e.target.value || null })}
            style={{ width: "100%" }}
          >
            <option value="">(generic)</option>
            <option value="planner">planner</option>
            <option value="specialist">specialist</option>
            <option value="tech_lead">tech_lead</option>
            <option value="architect">architect</option>
            <option value="code_reviewer">code_reviewer</option>
          </select>
        </section>

        {/* Description + system prompt */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>Behaviour</h3>
          <label className="small">Description</label>
          <input
            value={current.description ?? ""}
            onChange={(e) => void handleChange({ description: e.target.value })}
            style={{ marginBottom: 8 }}
          />
          <label className="small">System prompt</label>
          <textarea
            rows={4}
            value={current.system_prompt ?? ""}
            onChange={(e) => void handleChange({ system_prompt: e.target.value })}
            style={{ marginBottom: 8 }}
          />
          <label className="small">Max tool iterations</label>
          <input
            type="number"
            min={1}
            max={50}
            value={current.max_tool_iterations ?? 6}
            onChange={(e) => void handleChange({ max_tool_iterations: parseInt(e.target.value || "6", 10) })}
          />
          <label className="small" style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={current.is_reviewer ?? false}
              style={{ width: "auto" }}
              onChange={(e) => void handleChange({ is_reviewer: e.target.checked })}
            />
            Reviewer (merges branches on APPROVED)
          </label>
          <label className="small" style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={current.is_final_reviewer ?? false}
              style={{ width: "auto" }}
              onChange={(e) => void handleChange({ is_final_reviewer: e.target.checked })}
            />
            Final reviewer (runs last; gates on heavy_tier_mid_run_dispatch)
          </label>
        </section>

        {/* Model */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>Model</h3>
          <ModelPicker
            value={current.model_tier ?? "solid"}
            onChange={(tier) => void handleChange({ model_tier: tier })}
          />
        </section>

        {/* Tools — grouped accordion */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>Tools</h3>
          <GroupedToolPicker
            selected={current.declared_local_tools ?? []}
            onChange={(next) => void handleChange({ declared_local_tools: next })}
            onRbacChange={onRbacChange}
            archetypeRoleType={current.agent_class_role_type ?? null}
            projectRbacScopes={projectRbacScopes}
          />
        </section>

        {/* Dependencies */}
        <section style={{ marginBottom: 20 }}>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>Dependencies</h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {allRoles.filter((r) => r !== current.role).map((r) => {
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
                      void handleChange({ depends_on: next });
                    }}
                  />
                  {r}
                </label>
              );
            })}
          </div>
        </section>

        {/* Hierarchy */}
        {allRoles.filter((r) => r !== current.role).length > 0 && (
          <section style={{ marginBottom: 20 }}>
            <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>Hierarchy</h3>
            <label className="small">Manages (can assign tasks to)</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
              {allRoles.filter((r) => r !== current.role).map((r) => {
                const checked = (current.manages ?? []).includes(r);
                return (
                  <label key={r} className="small" style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      style={{ width: "auto" }}
                      onChange={() => {
                        const next = checked
                          ? (current.manages ?? []).filter((x) => x !== r)
                          : [...(current.manages ?? []), r];
                        void handleChange({ manages: next });
                      }}
                    />
                    {r}
                  </label>
                );
              })}
            </div>
            <label className="small">Escalates to</label>
            <select
              value={(current.escalates_to ?? [])[0] ?? ""}
              onChange={(e) => void handleChange({ escalates_to: e.target.value ? [e.target.value] : [] })}
              style={{ width: "100%" }}
            >
              <option value="">(default: tech_lead if present)</option>
              {allRoles.filter((r) => r !== current.role).map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </section>
        )}

        {/* RBAC (read-only) */}
        <section>
          <h3 className="small" style={{ textTransform: "uppercase", color: "var(--muted)" }}>Effective RBAC scopes</h3>
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
