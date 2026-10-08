import { useEffect, useId, useState } from "react";
import { resolveTheme, useTheme } from "../lib/theme";

/** The mermaid module, loaded on first use so pages without diagrams do not pay for it. */
type MermaidApi = typeof import("mermaid").default;
let loading: Promise<MermaidApi> | null = null;

function loadMermaid(): Promise<MermaidApi> {
  loading ??= import("mermaid").then((m) => m.default);
  return loading;
}

/**
 * A Mermaid diagram (design flows, role contracts plan §4.2). The text is written by agents, so
 * it renders with `securityLevel: "strict"` (no scripts, no HTML labels, click handlers off).
 * When it does not parse, the source is shown with the error instead.
 */
export function MermaidDiagram({ source, label }: { source: string; label: string }) {
  const [pref] = useTheme();
  const theme = resolveTheme(pref) === "dark" ? "dark" : "default";
  const id = `mmd-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSvg(null);
    setError(null);
    loadMermaid()
      .then(async (mermaid) => {
        // Plain SVG text labels: HTML labels (foreignObject) are hidden by the app's styles in
        // some diagram types, and keeping agent text out of HTML is safer anyway.
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme, htmlLabels: false,
          flowchart: { htmlLabels: false }, class: { htmlLabels: false } });
        const out = await mermaid.render(id, source);
        if (!cancelled) setSvg(out.svg);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [id, source, theme]);

  return (
    <figure className="m-0" aria-label={label}>
      {svg ? (
        // The SVG comes from mermaid in strict mode, which sanitises the agent-written text.
        <div className="overflow-x-auto [&_svg]:h-auto [&_svg]:max-w-full" data-testid="mermaid-svg"
          dangerouslySetInnerHTML={{ __html: svg }} />
      ) : error ? (
        <p className="m-0 text-xs text-destructive">The diagram could not be drawn: {error}</p>
      ) : (
        <p className="m-0 text-xs text-muted-foreground">Drawing the diagram…</p>
      )}
      {(error || showSource) && <pre className="mt-2 overflow-x-auto text-xs">{source}</pre>}
      {svg && (
        <button type="button" className="mt-1 text-xs text-muted-foreground underline"
          onClick={() => setShowSource((v) => !v)}>
          {showSource ? "Hide source" : "Show source"}
        </button>
      )}
    </figure>
  );
}
