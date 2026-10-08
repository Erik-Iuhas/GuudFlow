import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Alert, Input, Label, Select, Skeleton, Table, Td, Th } from "../../components/ui/form-controls";
import {
  EFFORT_LEVELS,
  TIERS,
  modelConfigApi,
  useConfigMutation,
  useModelConfig,
  type ClientKind,
  type ConfigDoc,
  type EffortLevel,
  type KeyStatus,
  type ModelWire,
  type ProviderWire,
  type TestResult,
  type Tier,
} from "./api";
import { envNameFor, fromPerMillion, parseWorkingContext, perMillion, tierModels, toDoc, toggleEffortLevel, parseEffortValues, formatEffortValues } from "./model";

const KEY_VARIANT: Record<KeyStatus, BadgeProps["variant"]> = {
  env: "complete",
  stored: "complete",
  local: "outline",
  missing: "waiting",
};

function keyLabel(p: ProviderWire): string {
  switch (p.key) {
    case "env": return `key from $${p.api_key_env}`;
    case "stored": return "key saved on this machine";
    case "local": return "local, no key needed";
    default: return "no key";
  }
}

const TIER_HELP: Record<Tier, string> = {
  heavy: "planner, overseer and other heavy roles",
  solid: "builders, reviewers, memory writer",
  fast: "quips and other quick calls",
};

const PRICE_FIELDS = [
  ["cost_per_1k_input", "Input"],
  ["cost_per_1k_output", "Output"],
  ["cache_read_per_1k", "Cache read"],
  ["cache_write_per_1k", "Cache write"],
] as const;
type PriceField = (typeof PRICE_FIELDS)[number][0];

/**
 * Settings → Models & providers: which model each tier uses, the providers and their keys
 * (stored on the host, never shown again), and per-model prices that budgets, cost previews
 * and the gateway's spend use.
 */
export function ModelSettings() {
  const config = useModelConfig();
  const [draft, setDraft] = useState<ConfigDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Start from the server's view; keep local edits until saved or discarded.
  const server = useMemo(() => (config.data ? toDoc(config.data) : null), [config.data]);
  const doc = draft ?? server;
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(server);

  const save = useConfigMutation(modelConfigApi.save);
  const reset = useConfigMutation(() => modelConfigApi.reset());

  useEffect(() => {
    if (!saved) return;
    const t = setTimeout(() => setSaved(false), 2500);
    return () => clearTimeout(t);
  }, [saved]);

  if (config.isLoading) return <Skeleton className="h-48" />;
  if (config.error || !config.data || !doc) {
    return <Alert variant="error">Could not load the model settings: {String(config.error?.message ?? "")}</Alert>;
  }
  const view = config.data;

  function edit(next: (d: ConfigDoc) => ConfigDoc) {
    setSaved(false);
    setDraft((d) => next(structuredClone(d ?? (server as ConfigDoc))));
  }

  function onSave() {
    if (!doc) return;
    setError(null);
    save.mutate(doc, {
      onSuccess: () => {
        setDraft(null);
        setSaved(true);
      },
      onError: (e) => setError(e.message),
    });
  }

  return (
    <section className="flex flex-col gap-6" aria-label="Models and providers" data-testid="model-settings">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="m-0 text-base font-semibold">Models &amp; providers</h2>
          <p className="m-0 mt-1 text-sm text-muted-foreground">
            Which model each tier uses, where keys come from, and the prices budgets and cost previews use.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={view.custom ? "secondary" : "outline"}>{view.custom ? "custom" : "shipped defaults"}</Badge>
          {view.custom && (
            <Button size="sm" variant="ghost" disabled={reset.isPending}
              onClick={() => {
                if (window.confirm("Go back to the shipped models and prices? Saved keys are kept.")) {
                  setDraft(null);
                  reset.mutate(undefined);
                }
              }}>
              Reset to defaults
            </Button>
          )}
        </div>
      </div>

      <TierPickers doc={doc} onChange={(tier, ids) => edit((d) => ({ ...d, tiers: { ...d.tiers, [tier]: ids } }))} />

      <Providers
        providers={view.providers}
        doc={doc}
        onAdd={(name, client, baseUrl, env) => edit((d) => ({
          ...d,
          providers: { ...d.providers, [name]: { client, api_key_env: env, ...(baseUrl ? { base_url: baseUrl } : {}) } },
        }))}
        onRemove={(name) => edit((d) => {
          const { [name]: _gone, ...rest } = d.providers;
          return { ...d, providers: rest, models: d.models.filter((m) => m.provider !== name) };
        })}
        dirty={dirty}
      />

      <Models doc={doc} onChange={(models) => edit((d) => ({
        ...d,
        models,
        // A removed model leaves every tier it was in.
        tiers: Object.fromEntries(Object.entries(d.tiers).map(([t, ids]) =>
          [t, (ids ?? []).filter((id) => models.some((m) => m.id === id))])),
      }))} />

      <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-background py-3">
        <Button onClick={onSave} disabled={!dirty || save.isPending}>
          {save.isPending ? "Saving…" : "Save models & prices"}
        </Button>
        <Button variant="ghost" onClick={() => { setDraft(null); setError(null); }} disabled={!dirty}>
          Discard changes
        </Button>
        {saved && <span className="text-sm text-status-complete" role="status">Saved. New runs use these settings.</span>}
        {dirty && !save.isPending && <span className="text-sm text-muted-foreground">Unsaved changes</span>}
      </div>
      {error && <Alert variant="error">{error}</Alert>}
    </section>
  );
}

function TierPickers({ doc, onChange }: { doc: ConfigDoc; onChange: (tier: Tier, ids: string[]) => void }) {
  const usable = doc.models.filter((m) => !m.deprecated);
  return (
    <div>
      <h3 className="m-0 mb-1 text-sm font-semibold">Tiers</h3>
      <p className="m-0 mb-2 text-xs text-muted-foreground">
        Pick one or more models per tier. With several, the tier&apos;s roles are spread across them and a
        reviewer gets a different model from the work it reviews when it can. The first is the default
        (used for cost estimates).
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        {TIERS.map((tier) => {
          const chosen = tierModels(doc.models, doc.tiers, tier);
          const rest = usable.filter((m) => !chosen.includes(m.id));
          return (
            <div key={tier} className="rounded-lg border border-border bg-card p-2.5" data-testid={`tier-${tier}`}>
              <div className="mb-1.5 flex items-baseline justify-between gap-2">
                <span className="text-sm font-semibold">{tier}</span>
                <span className="text-xs text-muted-foreground">{TIER_HELP[tier]}</span>
              </div>
              <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0" aria-label={`${tier} models`}>
                {chosen.map((id, i) => (
                  <li key={id}>
                    <Badge variant={i === 0 ? "secondary" : "outline"} className="gap-1 pr-1 font-mono">
                      {id}{i === 0 && chosen.length > 1 ? " · default" : ""}
                      {chosen.length > 1 && (
                        <button type="button" aria-label={`Remove ${id} from ${tier}`}
                          className="rounded px-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          onClick={() => onChange(tier, chosen.filter((x) => x !== id))}>×</button>
                      )}
                    </Badge>
                  </li>
                ))}
              </ul>
              {rest.length > 0 && (
                <Select aria-label={`Add a model to ${tier}`} value="" className="mt-2 h-8 py-0 text-xs"
                  onChange={(e) => e.target.value && onChange(tier, [...chosen, e.target.value])}>
                  <option value="">+ add a model…</option>
                  {rest.map((m) => (
                    <option key={m.id} value={m.id}>{m.id}{m.tier !== tier ? ` (${m.tier})` : ""}</option>
                  ))}
                </Select>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Providers({
  providers, doc, onAdd, onRemove, dirty,
}: {
  providers: ProviderWire[];
  doc: ConfigDoc;
  onAdd: (name: string, client: ClientKind, baseUrl: string, env: string) => void;
  onRemove: (name: string) => void;
  dirty: boolean;
}) {
  const saved = new Set(providers.map((p) => p.name));
  // Providers added in the draft show up before they are saved (no key actions yet).
  const pending = Object.entries(doc.providers).filter(([n]) => !saved.has(n));
  return (
    <div>
      <h3 className="m-0 mb-2 text-sm font-semibold">Providers</h3>
      <p className="m-0 mb-2 text-xs text-muted-foreground">
        Keys stay on this machine: they go to the LLM gateway, never into an agent's sandbox, and are never shown again.
        An environment variable, when set, wins over a saved key.
      </p>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {providers.filter((p) => p.name in doc.providers).map((p) => (
          <ProviderRow key={p.name} provider={p} models={doc.models.filter((m) => m.provider === p.name).length}
            onRemove={() => onRemove(p.name)} keysLocked={dirty} />
        ))}
        {pending.map(([name, p]) => (
          <li key={name} className="rounded-lg border border-dashed border-border p-3 text-sm">
            <span className="font-medium">{name}</span>{" "}
            <span className="text-muted-foreground">({p.client}{p.base_url ? `, ${p.base_url}` : ""}) — save to add its key</span>
            <Button size="sm" variant="ghost" className="ml-2" onClick={() => onRemove(name)}>Remove</Button>
          </li>
        ))}
      </ul>
      <AddProvider existing={Object.keys(doc.providers)} onAdd={onAdd} />
    </div>
  );
}

function ProviderRow({
  provider, models, onRemove, keysLocked,
}: {
  provider: ProviderWire;
  models: number;
  onRemove: () => void;
  keysLocked: boolean;
}) {
  const [key, setKey] = useState("");
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const setKeyM = useConfigMutation((k: string) => modelConfigApi.setKey(provider.name, k));
  const clearKeyM = useConfigMutation(() => modelConfigApi.clearKey(provider.name));
  const keyError = setKeyM.error ?? clearKeyM.error;

  async function runTest() {
    setTesting(true);
    setTest(null);
    try {
      setTest(await modelConfigApi.test(provider.name));
    } catch (e) {
      setTest({ ok: false, detail: e instanceof Error ? e.message : String(e), models: [] });
    } finally {
      setTesting(false);
    }
  }

  const [editing, setEditing] = useState(false);
  return (
    <li className="rounded-lg border border-border bg-card px-3 py-2 text-card-foreground" data-testid={`provider-${provider.name}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{provider.name}</span>
        <Badge variant="outline">{provider.client === "anthropic" ? "Anthropic" : "OpenAI-compatible"}</Badge>
        <Badge variant={KEY_VARIANT[provider.key]}>{keyLabel(provider)}</Badge>
        <span className="text-xs text-muted-foreground">{models} model{models === 1 ? "" : "s"}</span>
        {provider.base_url && <span className="truncate font-mono text-xs text-muted-foreground">{provider.base_url}</span>}
        <span className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => void runTest()} disabled={testing}>
            {testing ? "Testing…" : "Test"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing((v) => !v)} aria-expanded={editing}
            disabled={keysLocked} title={keysLocked ? "Save or discard your changes first" : undefined}>
            {provider.key === "stored" ? "Replace key" : "Set key"}
          </Button>
          {provider.key === "stored" && (
            <Button size="sm" variant="ghost" disabled={clearKeyM.isPending || keysLocked}
              onClick={() => clearKeyM.mutate(undefined)}>Forget key</Button>
          )}
          <Button size="sm" variant="ghost" onClick={onRemove}>Remove</Button>
        </span>
      </div>
      {editing && (
        <form
          className="mt-2 flex flex-wrap items-center gap-2"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (key.trim()) {
              setKeyM.mutate(key.trim(), { onSuccess: () => { setKey(""); setEditing(false); } });
            }
          }}
        >
          <Input type="password" autoComplete="off" className="max-w-xs" value={key} autoFocus
            aria-label={`API key for ${provider.name}`} placeholder="Paste an API key"
            onChange={(e) => setKey(e.target.value)} />
          <Button type="submit" size="sm" disabled={!key.trim() || setKeyM.isPending}>Save key</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => { setKey(""); setEditing(false); }}>Cancel</Button>
        </form>
      )}
      {keyError && <Alert variant="error" className="mt-2">{keyError.message}</Alert>}
      {test && (
        <p className={`m-0 mt-1 text-sm ${test.ok ? "text-status-complete" : "text-status-failed"}`} role="status">
          {test.ok ? "✓ " : "✕ "}{test.detail}
          {test.ok && test.models.length > 0 && (
            <span className="text-muted-foreground"> — {test.models.slice(0, 6).join(", ")}{test.models.length > 6 ? " …" : ""}</span>
          )}
        </p>
      )}
    </li>
  );
}

function AddProvider({
  existing, onAdd,
}: {
  existing: string[];
  onAdd: (name: string, client: ClientKind, baseUrl: string, env: string) => void;
}) {
  const [name, setName] = useState("");
  const [client, setClient] = useState<ClientKind>("openai_compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [env, setEnv] = useState("");
  const clean = name.trim().toLowerCase();
  const valid = /^[a-z0-9][a-z0-9_-]{0,39}$/.test(clean) && !existing.includes(clean)
    && (client === "anthropic" || /^https?:\/\/\S+$/.test(baseUrl.trim()));
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-sm text-muted-foreground">Add a provider</summary>
      <form
        className="mt-2 grid gap-2 rounded-lg border border-dashed border-border p-3 sm:grid-cols-4"
        aria-label="Add a provider"
        onSubmit={(e) => {
          e.preventDefault();
          if (!valid) return;
          onAdd(clean, client, baseUrl.trim(), env.trim() || envNameFor(clean));
          setName(""); setBaseUrl(""); setEnv("");
        }}
      >
        <div>
          <Label htmlFor="new-provider-name">Name</Label>
          <Input id="new-provider-name" placeholder="together" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="new-provider-kind">API</Label>
          <Select id="new-provider-kind" value={client} onChange={(e) => setClient(e.target.value as ClientKind)}>
            <option value="openai_compatible">OpenAI-compatible</option>
            <option value="anthropic">Anthropic</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="new-provider-url">Base URL</Label>
          <Input id="new-provider-url" placeholder="https://api.example.com/v1" value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="new-provider-env">Key env var (optional)</Label>
          <Input id="new-provider-env" placeholder={clean ? envNameFor(clean) : "TOGETHER_API_KEY"} value={env}
            onChange={(e) => setEnv(e.target.value)} />
        </div>
        <div className="sm:col-span-4">
          <Button type="submit" size="sm" disabled={!valid}>Add provider</Button>
        </div>
      </form>
    </details>
  );
}

function Models({ doc, onChange }: { doc: ConfigDoc; onChange: (models: ModelWire[]) => void }) {
  const providers = Object.keys(doc.providers);
  const [newId, setNewId] = useState("");
  const set = (i: number, patch: Partial<ModelWire>) =>
    onChange(doc.models.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  const setPrice = (i: number, field: PriceField, text: string) => {
    const value = fromPerMillion(text);
    if (value === null) return;
    const next = { ...doc.models[i] } as ModelWire;
    if (value === undefined) delete next[field];
    else next[field] = value;
    onChange(doc.models.map((m, j) => (j === i ? next : m)));
  };
  const canAdd = newId.trim().length > 0 && !doc.models.some((m) => m.id === newId.trim()) && providers.length > 0;
  return (
    <div>
      <h3 className="m-0 mb-1 text-sm font-semibold">Models and prices</h3>
      <p className="m-0 mb-2 text-xs text-muted-foreground">
        USD per million tokens. A blank cache-write price means 1.25× input. “Works in” is the
        context agents use before opencode compacts (blank: 200k, or the model's maximum if
        smaller); a bigger window costs more on every turn. “Effort” lists the levels the model
        takes and the one agents use unless the team or project picks another (blank: the provider's
        default); a level a model lacks is lowered to the next one it has. Where a provider uses
        other words (Mistral: none/high, Groq Qwen: none/default), map them, e.g. “low=none”.
      </p>
      <Table aria-label="Models">
        <thead>
          <tr>
            <Th>Model</Th>
            <Th>Provider</Th>
            <Th>Tier</Th>
            {PRICE_FIELDS.map(([, label]) => <Th key={label} className="text-right">{label}</Th>)}
            <Th className="text-right">Works in</Th>
            <Th>Effort</Th>
            <Th>Images</Th>
            <Th><span className="sr-only">Actions</span></Th>
          </tr>
        </thead>
        <tbody>
          {doc.models.map((m, i) => (
            <tr key={m.id} data-testid={`model-${m.id}`} className={m.deprecated ? "opacity-60" : undefined}>
              <Td className="font-mono text-xs">
                {m.id}{m.deprecated ? " (deprecated)" : ""}
                {m.long_context_threshold && (
                  <span className="block font-sans text-[11px] text-muted-foreground">
                    above {Math.round(m.long_context_threshold / 1000)}k: {perMillion(m.long_input_per_1k)} /{" "}
                    {perMillion(m.long_output_per_1k)}
                  </span>
                )}
              </Td>
              <Td>
                <Select aria-label={`Provider for ${m.id}`} value={m.provider} className="h-8 py-0 text-xs"
                  onChange={(e) => set(i, { provider: e.target.value })}>
                  {providers.map((p) => <option key={p} value={p}>{p}</option>)}
                </Select>
              </Td>
              <Td>
                <Select aria-label={`Tier for ${m.id}`} value={m.tier} className="h-8 py-0 text-xs"
                  onChange={(e) => set(i, { tier: e.target.value as Tier })}>
                  {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
                </Select>
              </Td>
              {PRICE_FIELDS.map(([field, label]) => (
                <Td key={field} className="text-right">
                  <PriceInput label={`${label} price for ${m.id}`} value={m[field]}
                    placeholder={field === "cache_write_per_1k" ? perMillion((m.cost_per_1k_input ?? 0) * 1.25) : "0"}
                    onCommit={(text) => setPrice(i, field, text)} />
                </Td>
              ))}
              <Td className="text-right">
                <ContextInput model={m} onCommit={(tokens) => {
                  const next: ModelWire = { ...m };
                  if (tokens === undefined) delete next.working_context_tokens;
                  else next.working_context_tokens = tokens;
                  onChange(doc.models.map((x, j) => (j === i ? next : x)));
                }} />
              </Td>
              <Td>
                <EffortCell model={m} onChange={(next) => onChange(doc.models.map((x, j) => (j === i ? next : x)))} />
              </Td>
              <Td>
                <input type="checkbox" className="h-4 w-4 accent-primary" aria-label={`${m.id} takes images`}
                  checked={Boolean(m.supports_images)} onChange={(e) => set(i, { supports_images: e.target.checked })} />
              </Td>
              <Td>
                <Button size="sm" variant="ghost" aria-label={`Remove ${m.id}`}
                  onClick={() => onChange(doc.models.filter((_, j) => j !== i))}>✕</Button>
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
      <form className="mt-2 flex flex-wrap items-end gap-2" aria-label="New model"
        onSubmit={(e) => {
          e.preventDefault();
          if (!canAdd) return;
          onChange([...doc.models, { id: newId.trim(), provider: providers[0]!, tier: "solid", supports_tools: true }]);
          setNewId("");
        }}>
        <div>
          <Label htmlFor="new-model-id">Add a model</Label>
          <Input id="new-model-id" placeholder="model id, e.g. gpt-5-mini" value={newId} className="w-64"
            onChange={(e) => setNewId(e.target.value)} />
        </div>
        <Button type="submit" size="sm" variant="outline" disabled={!canAdd}>Add</Button>
      </form>
    </div>
  );
}

const EFFORT_SHORT: Record<EffortLevel, string> = { low: "L", medium: "M", high: "H", xhigh: "XH", max: "Max" };

/** Which effort levels a model takes (toggles) and its default level. */
function EffortCell({ model, onChange }: { model: ModelWire; onChange: (next: ModelWire) => void }) {
  const levels = model.effort_levels ?? [];
  return (
    <span className="inline-flex items-center gap-1">
      {EFFORT_LEVELS.map((level) => {
        const on = levels.includes(level);
        return (
          <button key={level} type="button" aria-pressed={on} title={level}
            aria-label={`${model.id} takes ${level} effort`}
            className={`rounded border px-1 text-[11px] ${on ? "border-primary bg-primary/10" : "text-muted-foreground"}`}
            onClick={() => onChange(toggleEffortLevel(model, level))}>
            {EFFORT_SHORT[level]}
          </button>
        );
      })}
      {levels.length > 0 && (
        <Select aria-label={`Default effort for ${model.id}`} value={model.default_effort ?? ""}
          className="h-8 w-24 py-0 text-xs"
          onChange={(e) => {
            const next: ModelWire = { ...model };
            if (e.target.value) next.default_effort = e.target.value as EffortLevel;
            else delete next.default_effort;
            onChange(next);
          }}>
          <option value="">provider</option>
          {levels.map((l) => <option key={l} value={l}>{l}</option>)}
        </Select>
      )}
      {levels.length > 0 && <EffortValuesInput model={model} onChange={onChange} />}
    </span>
  );
}

/** The provider's own words for levels, typed as "low=none, high=default"; blank: sent as named. */
function EffortValuesInput({ model, onChange }: { model: ModelWire; onChange: (next: ModelWire) => void }) {
  const shown = formatEffortValues(model.effort_values);
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [shown]);
  const parsed = parseEffortValues(text, model.effort_levels ?? []);
  const commit = () => {
    if (parsed === null) return;
    const next: ModelWire = { ...model };
    if (Object.keys(parsed).length) next.effort_values = parsed;
    else delete next.effort_values;
    onChange(next);
  };
  return (
    <Input aria-label={`Provider words for ${model.id} effort levels`} value={text} placeholder="sent as named"
      title="The provider's word for a level, e.g. low=none" aria-invalid={parsed === null || undefined}
      className={`h-8 w-32 font-mono text-xs ${parsed === null ? "border-destructive" : ""}`}
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} />
  );
}

/** The working context in thousands of tokens; blank means the default. */
function ContextInput({ model, onCommit }: { model: ModelWire; onCommit: (tokens: number | undefined) => void }) {
  const shown = model.working_context_tokens ? String(Math.round(model.working_context_tokens / 1000)) : "";
  const [text, setText] = useState(shown);
  useEffect(() => setText(shown), [shown]);
  const max = model.max_context_tokens ?? 200_000;
  const parsed = parseWorkingContext(text, max);
  const placeholder = String(Math.round(Math.min(200_000, max) / 1000));
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Input aria-label={`Working context for ${model.id} (thousands of tokens)`} inputMode="numeric" value={text}
        placeholder={placeholder} aria-invalid={parsed === null || undefined}
        className={`h-8 w-16 text-right font-mono text-xs ${parsed === null ? "border-destructive" : ""}`}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => { if (parsed !== null) onCommit(parsed); }}
        onKeyDown={(e) => { if (e.key === "Enter" && parsed !== null) { e.preventDefault(); onCommit(parsed); } }} />
      k / {Math.round(max / 1000)}k
    </span>
  );
}

/** A per-million price field that commits on blur or Enter (so typing "0." is not rejected). */
function PriceInput({
  label, value, placeholder, onCommit,
}: {
  label: string;
  value: number | undefined;
  placeholder: string;
  onCommit: (text: string) => void;
}) {
  const [text, setText] = useState(perMillion(value));
  useEffect(() => setText(perMillion(value)), [value]);
  const invalid = fromPerMillion(text) === null;
  return (
    <Input aria-label={label} inputMode="decimal" value={text} placeholder={placeholder}
      aria-invalid={invalid || undefined}
      className={`ml-auto h-8 w-20 text-right font-mono text-xs ${invalid ? "border-destructive" : ""}`}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onCommit(text)}
      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onCommit(text); } }} />
  );
}
