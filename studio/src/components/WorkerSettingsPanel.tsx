import { useEffect, useState } from "react";
import type { AgentConfig, ModelTier } from "../types";
import { modelsApi, toolsApi, type ModelEntry } from "../api";
import { checkCls, checkLabelCls, errorCls, fieldCls, labelCls, sectionHeadingCls } from "../lib/field";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";

interface Props {
  agent: AgentConfig;
  allRoles?: string[];
  projectRbacScopes?: string[];
  onClose: () => void;
  onChange: (partial: Partial<AgentConfig>) => void;
  onSave: (partial: Partial<AgentConfig>) => Promise<void>;
  onRbacChange?: (scopesToAdd: string[], scopesToRemove: string[]) => void;
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
    _modelsPromise = Promise.resolve()
      .then(() => modelsApi.list())
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
        className={fieldCls}
        value={value}
        onChange={(e) =>
          onChange({ model_tier: e.target.value as ModelTier, model_id: "" })
        }
      >
        <option value="heavy">Heavy (claude-opus-4-7)</option>
        <option value="solid">Solid (claude-sonnet-4-6)</option>
        <option value="fast">Fast (claude-haiku-4-5)</option>
      </select>
    );
  }

  return (
    <div className="flex gap-2">
      <select
        className={cn(fieldCls, "w-36 shrink-0")}
        value={provider}
        onChange={(e) => handleProviderChange(e.target.value)}
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
        className={cn(fieldCls, "flex-1")}
        onChange={(e) => handleModelChange(e.target.value)}
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
    _toolsPromise = Promise.resolve()
      .then(() => toolsApi.list())
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
    return <p className="m-0 text-xs text-muted-foreground">Could not load tools (check bearer token).</p>;
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
    return <p className="m-0 text-xs text-muted-foreground">Loading tools…</p>;
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
          <div key={ns} className="mb-1 overflow-hidden rounded-md border border-border">
            <div
              className="flex cursor-pointer items-center gap-2 bg-muted px-2.5 py-1.5"
              onClick={() => toggleGroup(ns)}
            >
              <input
                type="checkbox"
                checked={allSelected}
                className={checkCls}
                onChange={(e) => { e.stopPropagation(); allSelected ? deselectAll(ns) : selectAll(ns); }}
                onClick={(e) => e.stopPropagation()}
                title={allSelected ? "Deselect all" : "Select all enabled"}
              />
              <span className="flex-1 text-xs font-semibold">
                {NAMESPACE_LABELS[ns] ?? ns}
              </span>
              <span className="text-xs text-muted-foreground">{nsSelected}/{nsTools.length}</span>
              <span className={cn("text-[10px] transition-transform", isOpen && "rotate-90")}>▶</span>
            </div>
            {isOpen && (
              <div className="py-1">
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
                        className={cn(
                          checkLabelCls,
                          "py-1 pl-7 pr-2.5",
                          t.baseline
                            ? isExcludedBaseline
                              ? "bg-destructive/10"
                              : isSelected && "bg-primary/10"
                            : offArchetype
                              ? "bg-status-blocked/15"
                              : isSelected && "bg-primary/10",
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          className={checkCls}
                          onChange={() => toggleTool(t)}
                        />
                        <span className="font-mono text-xs">{t.name}</span>
                        {t.baseline && (
                          <Badge variant={isExcludedBaseline ? "failed" : "secondary"} className="text-[10px]">
                            {isExcludedBaseline ? "baseline (off)" : "baseline"}
                          </Badge>
                        )}
                        {offArchetype && <Badge variant="blocked" className="text-[10px]">off-archetype</Badge>}
                      </label>
                      {showModelPicker && (
                        <div className="flex items-center gap-2 pb-1.5 pl-[60px] pr-2.5 pt-0.5 text-xs">
                          <span className="min-w-[50px] text-xs text-muted-foreground">Model:</span>
                          <select
                            className={cn(fieldCls, "flex-1")}
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
  const [draft, setDraft] = useState<Partial<AgentConfig>>({});
  const [saveError, setSaveError] = useState<string | null>(null);

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

  const current = merged();

  const [baselineScopes, setBaselineScopes] = useState<string[]>([]);
  useEffect(() => {
    toolsApi.list().then((tools) => {
      setBaselineScopes(tools.filter((t) => t.baseline).map((t) => t.scope));
    }).catch(() => {});
  }, []);

  const effectiveScopes = [
    ...baselineScopes.map((s) => ({ scope: s, source: "baseline" })),
    ...projectRbacScopes.map((s) => ({ scope: s, source: "project" })),
  ];

  const otherRoles = allRoles.filter((r) => r !== current.role);

  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[480px] max-w-full gap-0 overflow-y-auto p-6">
        <SheetHeader className="mb-4 pr-8">
          <SheetTitle>Agent settings — {current.role}</SheetTitle>
          <SheetDescription className="sr-only">Edit this agent's behaviour, model, tools and dependencies.</SheetDescription>
        </SheetHeader>

        {saveError && <div className={errorCls}>{saveError}</div>}

        {/* Identity */}
        <section className="mb-5">
          <h3 className={sectionHeadingCls}>Identity</h3>
          <div><strong>Role:</strong> {current.role}</div>
          {current.persona_id && <div className="mb-2 text-xs text-muted-foreground">Persona: {current.persona_id}</div>}
          <label className={labelCls}>Archetype</label>
          <select
            className={fieldCls}
            value={current.agent_class_role_type ?? ""}
            onChange={(e) => void handleChange({ agent_class_role_type: e.target.value || null })}
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
        <section className="mb-5">
          <h3 className={sectionHeadingCls}>Behaviour</h3>
          <label className={labelCls} htmlFor="worker-name">Name</label>
          <input
            id="worker-name"
            className={cn(fieldCls, "mb-1")}
            value={current.name ?? ""}
            placeholder="Picked for you when the team is saved"
            maxLength={40}
            onChange={(e) => void handleChange({ name: e.target.value })}
          />
          <p className="mb-2 mt-0 text-xs text-muted-foreground">
            Teammates call this worker by name in briefs, notes and reviews.
          </p>
          <label className={labelCls}>Description</label>
          <input
            className={cn(fieldCls, "mb-2")}
            value={current.description ?? ""}
            onChange={(e) => void handleChange({ description: e.target.value })}
          />
          <label className={labelCls}>System prompt</label>
          <textarea
            className={cn(fieldCls, "mb-2")}
            rows={4}
            value={current.system_prompt ?? ""}
            onChange={(e) => void handleChange({ system_prompt: e.target.value })}
          />
          <label className={labelCls}>Max tool iterations</label>
          <input
            className={fieldCls}
            type="number"
            min={1}
            max={50}
            value={current.max_tool_iterations ?? 6}
            onChange={(e) => void handleChange({ max_tool_iterations: parseInt(e.target.value || "6", 10) })}
          />
          <label className={cn(checkLabelCls, "mt-2 gap-1.5")}>
            <input
              type="checkbox"
              className={checkCls}
              checked={current.is_reviewer ?? false}
              onChange={(e) => void handleChange({ is_reviewer: e.target.checked })}
            />
            Reviewer (merges branches on APPROVED)
          </label>
          <label className={cn(checkLabelCls, "mt-2 gap-1.5")}>
            <input
              type="checkbox"
              className={checkCls}
              checked={current.is_final_reviewer ?? false}
              onChange={(e) => void handleChange({ is_final_reviewer: e.target.checked })}
            />
            Final reviewer (runs last; gates on heavy_tier_mid_run_dispatch)
          </label>
        </section>

        {/* Model */}
        <section className="mb-5">
          <h3 className={sectionHeadingCls}>Model</h3>
          <ModelPicker
            value={current.model_tier ?? "solid"}
            valueModelId={current.model_id ?? ""}
            onChange={(next) => void handleChange(next)}
          />
        </section>

        {/* Tools — grouped accordion */}
        <section className="mb-5">
          <h3 className={sectionHeadingCls}>Tools</h3>
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
        <section className="mb-5">
          <h3 className={sectionHeadingCls}>Dependencies</h3>
          <div className="flex flex-wrap gap-1.5">
            {otherRoles.map((r) => {
              const checked = (current.depends_on ?? []).includes(r);
              return (
                <label key={r} className={cn(checkLabelCls, "gap-1")}>
                  <input
                    type="checkbox"
                    className={checkCls}
                    checked={checked}
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
        {otherRoles.length > 0 && (
          <section className="mb-5">
            <h3 className={sectionHeadingCls}>Hierarchy</h3>
            <label className={labelCls}>Manages (can assign tasks to)</label>
            <div className="mb-2.5 flex flex-wrap gap-1.5">
              {otherRoles.map((r) => {
                const checked = (current.manages ?? []).includes(r);
                return (
                  <label key={r} className={cn(checkLabelCls, "gap-1")}>
                    <input
                      type="checkbox"
                      className={checkCls}
                      checked={checked}
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
            <label className={labelCls}>Escalates to</label>
            <select
              className={fieldCls}
              value={(current.escalates_to ?? [])[0] ?? ""}
              onChange={(e) => void handleChange({ escalates_to: e.target.value ? [e.target.value] : [] })}
            >
              <option value="">(default: tech_lead if present)</option>
              {otherRoles.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </section>
        )}

        {/* RBAC (read-only) */}
        <section>
          <h3 className={sectionHeadingCls}>Effective RBAC scopes</h3>
          <ul className="m-0 list-none p-0">
            {effectiveScopes.map(({ scope, source }) => (
              <li key={scope} className="mb-0.5 text-xs">
                <code className="font-mono">{scope}</code>
                <span className="ml-1.5 text-muted-foreground">from {source}</span>
              </li>
            ))}
            {effectiveScopes.length === 0 && (
              <li className="text-xs text-muted-foreground">No extra scopes (baseline only)</li>
            )}
          </ul>
        </section>
      </SheetContent>
    </Sheet>
  );
}
