import type { ReactNode } from "react";
import { renderHarnessBody } from "../../components/harness/renderers";
import { Badge } from "../../components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import type { CapabilitiesDetail, TaskColumn, TypedSignal } from "../../lib/runEvents.gen";
import { cn } from "../../lib/utils";
import type { RunState } from "../../stores/runEvents";
import { useRunStore } from "../../stores/runStoreContext";
import { formatUsd } from "./model";
import { VirtualFeed } from "./VirtualFeed";

const EMPTY_FEED: readonly TypedSignal[] = [];


// Static class names so Tailwind sees them; the colours are the --col-* tokens.
export const COLUMN_CLASS: Record<TaskColumn, string> = {
  todo: "border-col-todo/40 text-col-todo",
  in_progress: "border-col-in_progress/40 text-col-in_progress",
  review: "border-col-review/40 text-col-review",
  done: "border-col-done/40 text-col-done",
  blocked: "border-col-blocked/40 text-col-blocked",
  needs_attention: "border-col-needs_attention/40 text-col-needs_attention",
};

export const COLUMN_LABEL: Record<TaskColumn, string> = {
  todo: "To do",
  in_progress: "In progress",
  review: "Review",
  done: "Done",
  blocked: "Blocked",
  needs_attention: "Needs attention",
};

/** Capability toggles a role runs with, as `name: level` pairs (the "effective" set). */
export function capabilitySummary(c: CapabilitiesDetail): { on: number; total: number; lines: string[] } {
  const entries = Object.entries(c.effective ?? {});
  return {
    on: entries.filter(([, v]) => v !== "off").length,
    total: entries.length,
    lines: entries.map(([k, v]) => `${k}: ${v}`),
  };
}

/** Badge showing how many toggles are on; the tooltip lists every effective toggle. */
export function CapabilityBadge({ role, capabilities }: { role: string; capabilities: CapabilitiesDetail }) {
  const { on, total, lines } = capabilitySummary(capabilities);
  const warned = (capabilities.warnings ?? []).length > 0;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-testid={`capability-badge-${role}`}
          aria-label={`Capabilities for ${role}: ${lines.join(", ") || "none reported"}`}
          className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Badge variant={warned ? "blocked" : "outline"}>
            {warned && <span aria-hidden>⚠</span>}
            {capabilities.role_type || "caps"} · {on}/{total} on
          </Badge>
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm">
        <div className="font-semibold">{capabilities.role_type || role} capabilities</div>
        <ul className="m-0 list-none p-0 font-mono">
          {lines.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
        {capabilities.model && <div className="mt-1 text-muted-foreground">{capabilities.model}</div>}
        {(capabilities.warnings ?? []).map((w) => (
          <div key={w} className="mt-1 text-status-blocked">
            ⚠ {w}
          </div>
        ))}
      </TooltipContent>
    </Tooltip>
  );
}

function ToolRow({ signal }: { signal: TypedSignal }): ReactNode {
  return (
    <div className="min-w-0 overflow-hidden border-b border-border px-2 py-1 text-sm">
      {renderHarnessBody(signal) ?? <span className="text-muted-foreground">{signal.summary}</span>}
    </div>
  );
}

/** A role's last result as one plain line (the structured summary when there is one). */
export function resultSummary(s: RunState, role: string): string {
  const id = s.agents[role]?.lastResultSignalId;
  const sig = id ? s.byId.get(id) : undefined;
  const result = (sig?.data as { result?: { summary?: unknown } } | null | undefined)?.result;
  const text = typeof result?.summary === "string" ? result.summary : (sig?.summary ?? "");
  return text.replace(/\s+/g, " ").trim();
}

/** A role's tool calls and full last result (opened from its card or graph node). */
export function AgentDetail({ role }: { role: string }) {
  const agent = useRunStore((s) => s.agents[role]);
  const feed = useRunStore((s) => s.toolFeed[role]) ?? EMPTY_FEED;
  const resultSignal = useRunStore((s) => {
    const id = s.agents[role]?.lastResultSignalId;
    return id ? s.byId.get(id) : undefined;
  });
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid={`agent-detail-${role}`}>
      <div className="max-h-[40%] shrink-0 overflow-y-auto border-b border-border px-3 py-2 text-sm">
        {agent?.capabilities && (
          <div className="mb-2">
            <CapabilityBadge role={role} capabilities={agent.capabilities} />
          </div>
        )}
        <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
          <span>Last result</span>
          <span>
            {agent?.spawns ?? 0} turns · {agent?.toolCalls ?? 0} calls · {formatUsd(agent?.cost ?? 0)}
          </span>
        </div>
        {resultSignal ? (
          (renderHarnessBody(resultSignal) ?? <p className="m-0">{resultSignal.summary}</p>)
        ) : (
          <span className="text-muted-foreground">No result yet.</span>
        )}
      </div>
      <div className="px-3 pt-2 text-xs font-medium text-muted-foreground">Tool calls</div>
      <VirtualFeed
        className="min-h-0 flex-1"
        label={`${role} tool calls`}
        testId={`toolfeed-${role}`}
        items={feed}
        getKey={(s) => s.id}
        renderRow={(s) => <ToolRow signal={s} />}
        estimateSize={32}
        empty="No tool calls yet."
      />
    </div>
  );
}
