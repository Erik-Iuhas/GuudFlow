import { useEffect, useRef, useState } from "react";
import type { AgentConfig, ModelTier } from "../types";
import { modelsApi, type ModelEntry } from "../api";

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
  valueModelId,
  onChange,
}: {
  value: ModelTier;
  valueModelId?: string;
  onChange: (next: { model_tier: ModelTier; model_id: string }) => void;
}) {
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [provider, setProvider] = useState<string>("");

  useEffect(() => {
    fetchModels()
      .then((ms) => {
        setModels(ms);
        // Pick provider from the saved model_id when present so the dropdowns
        // hydrate to whatever the user previously chose. Falls back to a
        // provider that has a model matching the current tier, then anthropic.
        const explicit = valueModelId ? ms.find((m) => m.id === valueModelId) : undefined;
        if (explicit) {
          setProvider(explicit.provider);
          return;
        }
        const tierMatch = ms.find((m) => m.tier === value);
        setProvider(tierMatch?.provider ?? "anthropic");
      })
      .catch(() => {});
  }, []);

  const providers = [...new Set(models.map((m) => m.provider))];
  const providerModels = models.filter((m) => m.provider === provider);

  function handleProviderChange(p: string) {
    setProvider(p);
    // Prefer a model in the new provider that matches the current tier;
    // otherwise pick its first model. Either way emit BOTH tier + id so
    // the saved selection survives a reload.
    const sameTier = models.find((m) => m.provider === p && m.tier === value);
    const first = sameTier ?? models.find((m) => m.provider === p);
    if (first) onChange({ model_tier: first.tier as ModelTier, model_id: first.id });
  }

  function handleModelChange(modelId: string) {
    const m = models.find((m) => m.id === modelId);
    if (m) onChange({ model_tier: m.tier as ModelTier, model_id: m.id });
  }

  // Find the currently selected model: explicit id wins, else first
  // tier-matching model in the active provider.
  const explicitInProvider = valueModelId
    ? providerModels.find((m) => m.id === valueModelId)
    : undefined;
  const currentModel =
    explicitInProvider ?? providerModels.find((m) => m.tier === value) ?? providerModels[0];

  if (models.length === 0) {
    // Fallback while loading or if API unavailable. Tier-only emit is fine
    // here — model_id stays empty until the registry populates.
    return (
      <select
        value={value}
        onChange={(e) =>
          onChange({ model_tier: e.target.value as ModelTier, model_id: "" })
        }
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
  // Track 12 — three sub-LLM tools share one group so the per-tool model
  // dropdown lives somewhere coherent in the UI.
  sub_llm: "Sub-LLM Dispatch",
};

// Track 12 — sub-LLM dispatch scopes: llm-dispatch, summarise-subtask,
// protocol-cleanup. Folded under one synthetic namespace so they don't
// scatter into three single-tool groups.
const SUB_LLM_SCOPES = new Set(["llm-dispatch", "summarise-subtask", "protocol-cleanup"]);

function toolNamespace(tool: ToolEntry): string {
  if (SUB_LLM_SCOPES.has(tool.scope)) return "sub_llm";
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
  excludedBaseline,
  onExcludedBaselineChange,
  subLlmModels,
  onSubLlmModelsChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
  onRbacChange?: (add: string[], remove: string[]) => void;
  archetypeRoleType: string | null;
  projectRbacScopes: string[];
  /** Track 12 — explicit baseline opt-out list. Un-checking a baseline tool
   *  pushes its scope here. Empty by default. */
  excludedBaseline?: string[];
  onExcludedBaselineChange?: (next: string[]) => void;
  /** Track 12 — per-agent sub-LLM model overrides. Surfaced as a small
   *  dropdown below the llm-dispatch checkbox when granted. */
  subLlmModels?: Record<string, string>;
  onSubLlmModelsChange?: (next: Record<string, string>) => void;
}) {
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());
  const [error, setError] = useState(false);
  // Track 12 — model registry for the per-agent sub-LLM dropdown shown
  // beneath llm-dispatch when granted. Loaded once; failure leaves the
  // dropdown empty (user falls back to project / auto pick).
  const [models, setModels] = useState<ModelEntry[]>([]);

  useEffect(() => {
    fetchTools()
      .then(setTools)
      .catch(() => setError(true));
    modelsApi.list().then(setModels).catch(() => {/* noop */});
  }, []);

  const excludedSet = new Set(excludedBaseline ?? []);
  function toggleBaselineExclude(scope: string) {
    if (!onExcludedBaselineChange) return;
    if (excludedSet.has(scope)) {
      onExcludedBaselineChange((excludedBaseline ?? []).filter((s) => s !== scope));
    } else {
      onExcludedBaselineChange([...(excludedBaseline ?? []), scope]);
    }
  }

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
    // Track 12 — baseline tools are toggleable now (writes to the
    // excluded list, not to selected).
    if (t.baseline) {
      toggleBaselineExclude(t.scope);
      return;
    }
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
    // Track 12 — also re-include any baselines in this group the agent had opted out of.
    if (onExcludedBaselineChange) {
      const baselineScopes = new Set(
        nsTools.filter((t) => t.baseline).map((t) => t.scope),
      );
      const next = (excludedBaseline ?? []).filter((s) => !baselineScopes.has(s));
      if (next.length !== (excludedBaseline ?? []).length) {
        onExcludedBaselineChange(next);
      }
    }
  }

  function deselectAll(ns: string) {
    const nsTools = groups.get(ns) ?? [];
    const nsScopes = new Set(nsTools.filter((t) => !t.baseline).map((t) => t.scope));
    const offArchetypeRemove = nsTools.filter((t) => !t.baseline && isOffArchetype(t) && selected.includes(t.scope)).map((t) => t.scope);
    onChange(selected.filter((s) => !nsScopes.has(s)));
    if (offArchetypeRemove.length) onRbacChange?.([], offArchetypeRemove);
    // Track 12 — also opt this agent out of every baseline tool in the group.
    if (onExcludedBaselineChange) {
      const baselineScopes = nsTools.filter((t) => t.baseline).map((t) => t.scope);
      const next = [...new Set([...(excludedBaseline ?? []), ...baselineScopes])];
      onExcludedBaselineChange(next);
    }
  }

  if (tools.length === 0) {
    return <p className="muted small">Loading tools…</p>;
  }

  return (
    <div>
      {orderedKeys.map((ns) => {
        const nsTools = groups.get(ns) ?? [];
        // Track 12 fix — baseline tools are "on" by default unless the user
        // explicitly excluded them. The old counter only counted entries in
        // `selected`, so a baseline-heavy group (Code Execution: 2 of 3
        // baseline) showed "0/3" even when all baselines were granted.
        const nsSelected = nsTools.filter((t) =>
          t.baseline ? !excludedSet.has(t.scope) : selected.includes(t.scope),
        ).length;
        const nsNonBaseline = nsTools.filter((t) => !t.baseline);
        const nsBaseline = nsTools.filter((t) => t.baseline);
        const isOpen = openGroups.has(ns);
        // Track 12 — group header reflects "every tool granted":
        //  - every non-baseline must be in selected, AND
        //  - no baseline in this group is on the excluded list.
        const allSelected =
          nsNonBaseline.every((t) => selected.includes(t.scope))
          && nsBaseline.every((t) => !excludedSet.has(t.scope));
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
                  const isExcludedBaseline = t.baseline && excludedSet.has(t.scope);
                  // A baseline tool is "on" iff not explicitly excluded.
                  const isSelected = t.baseline
                    ? !isExcludedBaseline
                    : selected.includes(t.scope);
                  const archLabel = archetypeRoleType ?? "this role";
                  const title = t.baseline
                    ? (isExcludedBaseline
                        ? "Baseline — opted out for this agent. Re-check to grant."
                        : "Baseline — always granted by default. Un-check to opt this agent out.")
                    : offArchetype
                      ? `Unusual for a ${archLabel} — grants ${t.scope} to this role for this run.`
                      : t.description;
                  // Track 12 — for the three sub-LLM dispatch tools, show a
                  // per-agent model dropdown beneath the row when granted.
                  const isSubLlmTool = (
                    t.scope === "llm-dispatch"
                    || t.scope === "summarise-subtask"
                    || t.scope === "protocol-cleanup"
                  );
                  const showModelPicker =
                    isSubLlmTool
                    && isSelected
                    && !!onSubLlmModelsChange;
                  const currentModelId = (subLlmModels ?? {})[t.scope] ?? "";
                  return (
                    <div key={t.scope}>
                      <label
                        title={title}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "4px 10px 4px 28px",
                          cursor: "pointer",
                          background: t.baseline
                            ? (isExcludedBaseline ? "rgba(239,68,68,.08)" : (isSelected ? "#eef2ff" : "transparent"))
                            : offArchetype
                              ? "rgba(251,146,60,.12)"
                              : isSelected
                                ? "#eef2ff"
                                : "transparent",
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          style={{ width: "auto" }}
                          onChange={() => toggleTool(t)}
                        />
                        <span style={{ fontFamily: "monospace", fontSize: 12 }}>{t.name}</span>
                        {t.baseline && (
                          <span
                            className="tag small"
                            style={{
                              fontSize: 10,
                              background: isExcludedBaseline ? "#fee2e2" : undefined,
                              color: isExcludedBaseline ? "#991b1b" : undefined,
                            }}
                          >
                            {isExcludedBaseline ? "baseline (off)" : "baseline"}
                          </span>
                        )}
                        {offArchetype && <span className="tag small" style={{ fontSize: 10, background: "#fed7aa", color: "#92400e", border: "none" }}>off-archetype</span>}
                      </label>
                      {showModelPicker && (
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                            padding: "2px 10px 6px 60px",
                            fontSize: 12,
                          }}
                        >
                          <span className="muted small" style={{ minWidth: 50 }}>Model:</span>
                          <select
                            value={currentModelId}
                            onChange={(e) => {
                              const next = { ...(subLlmModels ?? {}) };
                              if (e.target.value) {
                                next[t.scope] = e.target.value;
                              } else {
                                delete next[t.scope];
                              }
                              onSubLlmModelsChange!(next);
                            }}
                            style={{ flex: 1 }}
                          >
                            <option value="">(inherit project / auto)</option>
                            {(() => {
                              const grouped: Record<string, ModelEntry[]> = {};
                              for (const m of models) {
                                (grouped[m.provider] ||= []).push(m);
                              }
                              return Object.entries(grouped).sort().map(([prov, ms]) => (
                                <optgroup key={prov} label={prov}>
                                  {ms.map((m) => (
                                    <option key={m.id} value={m.id}>
                                      {m.id}
                                    </option>
                                  ))}
                                </optgroup>
                              ));
                            })()}
                          </select>
                        </div>
                      )}
                    </div>
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
            valueModelId={current.model_id ?? ""}
            onChange={(next) => void handleChange(next)}
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
            excludedBaseline={current.excluded_baseline_tools ?? []}
            onExcludedBaselineChange={(next) =>
              void handleChange({ excluded_baseline_tools: next })
            }
            subLlmModels={current.sub_llm_models_override ?? {}}
            onSubLlmModelsChange={(next) =>
              void handleChange({ sub_llm_models_override: next })
            }
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
