import type { ConfigDoc, ModelConfigView, ModelWire } from "./api";

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

/** The model a tier uses: the saved choice, else the first non-deprecated model of that tier. */
export function tierModel(models: readonly ModelWire[], tiers: ConfigDoc["tiers"], tier: string): string {
  const chosen = tiers[tier as keyof ConfigDoc["tiers"]];
  if (chosen && models.some((m) => m.id === chosen)) return chosen;
  return models.find((m) => m.tier === tier && !m.deprecated)?.id ?? "";
}
