import { EFFORT_LEVELS, type ConfigDoc, type EffortLevel, type ModelConfigView, type ModelWire } from "./api";

/** USD per 1k tokens -> USD per million tokens, for display (rounded to cents of a cent). */
export function perMillion(per1k: number | undefined): string {
  if (per1k === undefined || per1k === null) return "";
  return String(Math.round(per1k * 1000 * 10000) / 10000);
}

/** A typed USD-per-million value -> per 1k; empty -> undefined; not a number -> null. */
export function fromPerMillion(text: string): number | undefined | null {
  const t = text.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n / 1000 : null;
}

/** The editable document from the server's view (keys and status left out). */
export function toDoc(view: ModelConfigView): ConfigDoc {
  return {
    providers: Object.fromEntries(view.providers.map((p) => [p.name, {
      client: p.client, api_key_env: p.api_key_env, ...(p.base_url ? { base_url: p.base_url } : {}),
    }])),
    models: view.models.map((m) => ({ ...m })),
    tiers: { ...view.tiers },
  };
}

/** The default env var name for a new provider: "my-llm" -> "MY_LLM_API_KEY". */
export function envNameFor(provider: string): string {
  return `${provider.toUpperCase().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, "")}_API_KEY`;
}

/**
 * The models a tier uses: the saved choice (known models only), else the first
 * non-deprecated model of that tier. The first one is the tier's default.
 */
export function tierModels(models: readonly ModelWire[], tiers: ConfigDoc["tiers"], tier: string): string[] {
  const chosen = (tiers[tier as keyof ConfigDoc["tiers"]] ?? []).filter((id) => models.some((m) => m.id === id));
  if (chosen.length) return chosen;
  const first = models.find((m) => m.tier === tier && !m.deprecated)?.id;
  return first ? [first] : [];
}

/** Thousands of tokens typed into the "Works in" field → tokens; undefined for blank (the
 * default), null when it is not a whole number between 1k and the model's maximum. */
export function parseWorkingContext(text: string, maxTokens: number): number | undefined | null {
  const t = text.trim();
  if (t === "") return undefined;
  if (!/^\d+$/.test(t)) return null;
  const tokens = Number(t) * 1000;
  return tokens >= 1000 && tokens <= maxTokens ? tokens : null;
}

/** The model with `level` switched on or off in its effort levels (kept in order); switching off
 * the default level clears the default. */
export function toggleEffortLevel(model: ModelWire, level: EffortLevel): ModelWire {
  const on = new Set(model.effort_levels ?? []);
  if (on.has(level)) on.delete(level);
  else on.add(level);
  const next: ModelWire = { ...model };
  const levels = EFFORT_LEVELS.filter((l) => on.has(l));
  if (levels.length) next.effort_levels = levels;
  else delete next.effort_levels;
  if (next.default_effort && !on.has(next.default_effort)) delete next.default_effort;
  return next;
}
