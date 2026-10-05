import { memo, type ReactNode } from "react";
import { renderHarnessBody } from "../../components/harness/renderers";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import type { CapabilitiesDetail, TaskColumn, TypedSignal } from "../../lib/runEvents.gen";
import { cn } from "../../lib/utils";
import type { AgentState } from "../../stores/runEvents";
import { useRunStore } from "../../stores/runStoreContext";
import { STATE_LABEL, formatUsd } from "./model";
import { VirtualFeed } from "./VirtualFeed";

const EMPTY_FEED: readonly TypedSignal[] = [];

const STATE_VARIANT: Record<AgentState, BadgeProps["variant"]> = {
  idle: "outline",
  running: "running",
  complete: "complete",
  failed: "failed",
  blocked: "blocked",
  waiting: "waiting",
};

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
    <div className="border-b border-border px-2 py-1 text-sm">
      {renderHarnessBody(signal) ?? <span className="text-muted-foreground">{signal.summary}</span>}
    </div>
  );
}

export interface AgentLaneProps {
  role: string;
  selected?: boolean;
}

/** One agent's column: state, capability badge, current card, streaming tool calls, last result. */
export const AgentLane = memo(function AgentLane({ role, selected }: AgentLaneProps) {
  const agent = useRunStore((s) => s.agents[role]);
  const feed = useRunStore((s) => s.toolFeed[role]) ?? EMPTY_FEED;
  const card = useRunStore((s) => {
    const id = s.agents[role]?.card;
    return id ? s.cards[id] : undefined;
  });
  const resultSignal = useRunStore((s) => {
    const id = s.agents[role]?.lastResultSignalId;
    return id ? s.byId.get(id) : undefined;
  });
  const state: AgentState = agent?.state ?? "idle";

  return (
    <section
      role="region"
      aria-label={role}
      data-testid={`lane-${role}`}
      data-state={state}
      className={cn(
        "flex h-full min-h-0 w-80 shrink-0 flex-col rounded-lg border bg-card text-card-foreground",
        selected ? "border-ring ring-2 ring-ring" : "border-border",
      )}
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <h3 className="m-0 truncate text-sm font-semibold">{role}</h3>
        <Badge variant={STATE_VARIANT[state]}>{STATE_LABEL[state]}</Badge>
        {agent?.capabilities && <CapabilityBadge role={role} capabilities={agent.capabilities} />}
      </header>

      <div className="border-b border-border px-3 py-2 text-xs" data-testid={`lane-card-${role}`}>
        {card ? (
          <div className="flex items-center gap-2">
            <Badge variant="outline" className={cn("shrink-0", COLUMN_CLASS[card.column])}>
              {card.id}
            </Badge>
            <span className="truncate" title={card.title}>
              {card.title}
            </span>
            {card.round > 0 && <span className="shrink-0 text-muted-foreground">round {card.round}</span>}
          </div>
        ) : (
          <span className="text-muted-foreground">No active card</span>
        )}
      </div>

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

      <footer className="max-h-56 overflow-y-auto border-t border-border px-3 py-2 text-sm" data-testid={`lane-result-${role}`}>
        <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
          <span>Last result</span>
          <span>
            {agent?.toolCalls ?? 0} calls · {formatUsd(agent?.cost ?? 0)}
          </span>
        </div>
        {resultSignal ? (
          (renderHarnessBody(resultSignal) ?? <p className="m-0">{resultSignal.summary}</p>)
        ) : (
          <span className="text-muted-foreground">No result yet.</span>
        )}
      </footer>
    </section>
  );
});
