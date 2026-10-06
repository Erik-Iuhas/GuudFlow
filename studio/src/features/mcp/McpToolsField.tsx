import { useEffect, useMemo, useState } from "react";
import type { AgentConfig } from "../../types";
import { Badge } from "../../components/ui/badge";
import { checkCls, checkLabelCls } from "../../lib/field";
import { mcpApi, type McpServerWire } from "./api";
import { effectiveTools, inferRoleType, offeredTools } from "./model";

/** Third-party MCP tools (docs/MCP_PLAN.md) the effective list for an agent, as chips. */
export function McpToolChips({ names }: { names: string[] }) {
  if (names.length === 0) return <span className="text-xs text-muted-foreground">No MCP tools</span>;
  return (
    <ul className="m-0 flex list-none flex-wrap gap-1 p-0" aria-label="MCP tools">
      {names.map((n) => (
        <li key={n}><Badge variant="outline" className="font-mono">{n}</Badge></li>
      ))}
    </ul>
  );
}

/**
 * The agent's MCP tools: "Defaults for this role" stores `mcp_tools: null` (the registry's
 * defaults for the role type); "Choose" stores an explicit list. Widening beyond the defaults
 * needs the project's Owner and applies from the next session (enforced by the backend).
 */
export function McpToolsField({
  agent, onChange, servers: given,
}: {
  agent: AgentConfig;
  onChange: (partial: Partial<AgentConfig>) => void;
  /** Supplied by tests; otherwise loaded from the registry. */
  servers?: McpServerWire[];
}) {
  const [loaded, setLoaded] = useState<McpServerWire[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (given) return;
    let live = true;
    mcpApi.list().then((s) => live && setLoaded(s)).catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [given]);

  const servers = given ?? loaded;
  const offers = useMemo(() => offeredTools(servers ?? []), [servers]);
  const roleType = inferRoleType(agent);
  const explicit = agent.mcp_tools != null;
  const effective = effectiveTools(agent.mcp_tools, roleType, offers);

  if (!servers) {
    return <p className="m-0 text-xs text-muted-foreground">{failed ? "MCP servers unavailable." : "Loading MCP servers…"}</p>;
  }
  if (offers.length === 0) {
    return (
      <p className="m-0 text-xs text-muted-foreground">
        No MCP tools are offered yet. Add a server in Settings → MCP servers.
      </p>
    );
  }

  const byServer = offers.reduce<Record<string, typeof offers>>((acc, o) => {
    (acc[o.server] ??= []).push(o);
    return acc;
  }, {});
  const chosen = new Set(agent.mcp_tools ?? []);

  return (
    <div data-testid="mcp-tools-field">
      <div className="mb-2 flex flex-col gap-1" role="radiogroup" aria-label="MCP tools source">
        <label className={`${checkLabelCls} gap-1.5`}>
          <input type="radio" className={checkCls} name={`mcp-mode-${agent.role}`} checked={!explicit}
            onChange={() => onChange({ mcp_tools: null })} />
          Defaults for this role ({roleType})
        </label>
        <label className={`${checkLabelCls} gap-1.5`}>
          <input type="radio" className={checkCls} name={`mcp-mode-${agent.role}`} checked={explicit}
            onChange={() => onChange({ mcp_tools: effective })} />
          Choose
        </label>
      </div>

      {explicit && (
        <div className="mb-2 flex flex-col gap-2 rounded-md border border-border p-2">
          {Object.entries(byServer).map(([server, tools]) => (
            <fieldset key={server} className="m-0 border-0 p-0">
              <legend className="mb-1 p-0 text-xs font-semibold">{server}</legend>
              {tools.map((t) => (
                <label key={t.name} className={`${checkLabelCls} mb-0.5 gap-1.5`} title={t.description}>
                  <input type="checkbox" className={checkCls} checked={chosen.has(t.name)}
                    onChange={(e) => onChange({
                      mcp_tools: e.target.checked
                        ? [...(agent.mcp_tools ?? []), t.name]
                        : (agent.mcp_tools ?? []).filter((n) => n !== t.name),
                    })} />
                  <span className="font-mono">{t.tool}</span>
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      )}

      <McpToolChips names={effective} />
      <p className="m-0 mt-1 text-xs text-muted-foreground">
        Widening beyond the defaults needs the project&apos;s Owner and applies from the next session.
      </p>
    </div>
  );
}
