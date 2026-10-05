import { useMemo, useState } from "react";
import { HARNESS_SIGNAL_TYPES, type Signal } from "../types";
import { cn } from "../lib/utils";
import { fieldCls } from "../lib/field";
import { Badge } from "./ui/badge";
import { renderHarnessBody } from "./harness/renderers";

// Signal types grouped into logical buckets for the filter bar. `chip` colours
// the active filter button from the status tokens.
const TYPE_GROUPS: { label: string; types: string[]; chip: string }[] = [
  { label: "Status",   types: ["STARTED", "COMPLETE", "FAILED", "REJECTED", "REJECTED_RETRYING"], chip: "border-status-running text-status-running data-[active=true]:bg-status-running" },
  { label: "Protocol", types: ["PROTOCOL", "COMMIT", "VERIFY_RESULT", "ASK", "ANSWER", "SKIP_REQUEST", "SKIP_GRANTED", "SKIP_DENIED"], chip: "border-col-todo text-col-todo data-[active=true]:bg-col-todo" },
  { label: "Tasks",    types: ["TASK_ASSIGNED", "TASK_COMPLETE", "TASK_CLOSED", "STORY_OPENED", "STORY_CLOSED"], chip: "border-status-complete text-status-complete data-[active=true]:bg-status-complete" },
  { label: "Git",      types: ["GIT_COMMIT", "GIT_MERGE"], chip: "border-status-blocked text-status-blocked data-[active=true]:bg-status-blocked" },
  { label: "Harness", types: [...HARNESS_SIGNAL_TYPES], chip: "border-status-waiting text-status-waiting data-[active=true]:bg-status-waiting" },
  { label: "User",    types: ["USER_COMMENT", "APPROVAL_NEEDED", "APPROVAL_GRANTED", "APPROVAL_DENIED", "HUMAN_NEEDED", "HUMAN_RESPONDED"], chip: "border-status-failed text-status-failed data-[active=true]:bg-status-failed" },
];

const ALL_GROUP_LABELS = TYPE_GROUPS.map((g) => g.label);

function typeToGroup(type: string): string | null {
  for (const g of TYPE_GROUPS) {
    if (g.types.includes(type)) return g.label;
  }
  return null;
}

/** The fields the feed reads. A raw `Signal` and the store's `TypedSignal` both satisfy it. */
export type FeedSignal = Pick<Signal, "id" | "type" | "summary" | "detail" | "from_agent" | "iteration" | "timestamp"> & {
  from_user?: boolean;
};

export interface BoardFilterState<T extends FeedSignal> {
  /** Groups present in the signal set, in filter-bar order. */
  presentGroups: string[];
  activeGroups: Set<string>;
  agents: string[];
  agentFilter: string;
  toggleGroup: (label: string) => void;
  setAgentFilter: (agent: string) => void;
  visible: T[];
  board: T[];
}

/** Group + agent filtering shared by `BoardFeed` and the cockpit's virtualized feed. */
export function useBoardFilter<T extends FeedSignal>(signals: readonly T[]): BoardFilterState<T> {
  const board = useMemo(() => signals.filter((s) => !isQuip(s)), [signals]);

  // Which groups are present in the current signal set
  const presentGroups = useMemo(() => {
    const seen = new Set<string>();
    for (const s of board) {
      const g = typeToGroup(s.type);
      if (g) seen.add(g);
      else seen.add("Other");
    }
    return ALL_GROUP_LABELS.filter((g) => seen.has(g));
  }, [board]);

  const [activeGroups, setActiveGroups] = useState<Set<string>>(
    () => new Set(ALL_GROUP_LABELS),
  );

  const [agentFilter, setAgentFilter] = useState<string>("all");

  // Unique agents for the agent filter
  const agents = useMemo(() => {
    const seen = new Set<string>();
    for (const s of board) if (s.from_agent) seen.add(s.from_agent);
    return [...seen].sort();
  }, [board]);

  function toggleGroup(label: string) {
    setActiveGroups((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }

  const visible = useMemo(
    () =>
      board.filter((s) => {
        const g = typeToGroup(s.type) ?? "Other";
        if (!activeGroups.has(g) && !activeGroups.has("Other")) return false;
        if (g === "Other" && !activeGroups.has("Other")) return false;
        if (!activeGroups.has(g)) return false;
        if (agentFilter !== "all" && s.from_agent !== agentFilter && !s.from_user) return false;
        return true;
      }),
    [board, activeGroups, agentFilter],
  );

  return { presentGroups, activeGroups, agents, agentFilter, toggleGroup, setAgentFilter, visible, board };
}

export function BoardFilterBar<T extends FeedSignal>({ filter }: { filter: BoardFilterState<T> }) {
  const { presentGroups, activeGroups, agents, agentFilter, toggleGroup, setAgentFilter } = filter;
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2">
      {presentGroups.map((label) => {
        const group = TYPE_GROUPS.find((g) => g.label === label);
        const active = activeGroups.has(label);
        return (
          <button
            key={label}
            type="button"
            onClick={() => toggleGroup(label)}
            aria-pressed={active}
            data-active={active}
            className={cn(
              "cursor-pointer rounded-xl border bg-transparent px-2 py-0.5 text-[11px] font-semibold transition-colors data-[active=true]:text-background",
              group?.chip ?? "border-muted-foreground text-muted-foreground data-[active=true]:bg-muted-foreground",
            )}
          >
            {label}
          </button>
        );
      })}
      {agents.length > 1 && (
        <select
          value={agentFilter}
          onChange={(e) => setAgentFilter(e.target.value)}
          aria-label="Filter by agent"
          className={cn(fieldCls, "ml-auto w-auto px-1.5 py-0.5 text-[11px]")}
        >
          <option value="all">All agents</option>
          {agents.map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
      )}
    </div>
  );
}

export function BoardFeed({ signals }: { signals: Signal[] }) {
  const filter = useBoardFilter(signals);
  const { visible, board } = filter;

  return (
    <div>
      <BoardFilterBar filter={filter} />

      <div className="flex flex-col gap-1.5">
        {visible.length === 0 && (
          <p className="m-0 text-xs text-muted-foreground">
            {board.length === 0
              ? "No signals yet — waiting for the run to start…"
              : "No signals match the active filters."}
          </p>
        )}
        {visible.map((s) => (
          <SignalRow key={s.id} signal={s} />
        ))}
      </div>
    </div>
  );
}

/** One board row. Exported for the cockpit's virtualized feed. */
export function SignalRow({ signal }: { signal: FeedSignal }) {
  const time = signal.timestamp?.replace("T", " ").split(".")[0]?.slice(11) ?? "";
  const kind = classify(signal);
  // Structured body for opencode harness signals; null (bad or missing JSON,
  // other types) falls back to the plain summary + raw detail below.
  const harness = renderHarnessBody(signal);
  return (
    <div
      data-signal
      data-kind={kind || undefined}
      className={cn(
        "rounded-md border border-border bg-card px-3 py-2.5 text-[13px] text-card-foreground",
        kind === "user" && "border-l-[3px] border-l-status-blocked",
        kind === "protocol" && "bg-muted",
      )}
    >
      <div className="mb-0.5 flex items-center gap-2 text-xs text-muted-foreground">
        <span className="font-mono">{time}</span>
        <Badge variant={statusVariant(signal.type)} className="rounded">
          {signal.type}
        </Badge>
        {signal.from_agent && <span>{signal.from_agent}</span>}
        {signal.iteration > 0 && <span>iter {signal.iteration}</span>}
      </div>
      {harness ? (
        <>
          {signal.type !== "TOOL_CALL" && <div className="font-medium">{signal.summary}</div>}
          {harness}
        </>
      ) : (
        <>
          <div className="font-medium">{signal.summary}</div>
          {signal.detail && (
            <pre className="m-0 mt-1.5 overflow-x-auto rounded bg-muted p-2 font-mono text-[11px] text-foreground">{signal.detail}</pre>
          )}
        </>
      )}
    </div>
  );
}

function classify(s: FeedSignal): string {
  if (s.from_user) return "user";
  if (s.type === "PROTOCOL") return "protocol";
  return "";
}

function statusVariant(type: string): "complete" | "failed" | "running" | "waiting" {
  if (type === "COMPLETE" || type === "APPROVED") return "complete";
  if (type === "FAILED" || type === "REJECTED") return "failed";
  if (type === "STARTED" || type === "THINKING") return "running";
  return "waiting";
}

function isQuip(_s: FeedSignal): boolean {
  return false;
}
