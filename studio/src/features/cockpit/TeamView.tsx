import { cn } from "../../lib/utils";
import type { AgentState } from "../../stores/runEvents";
import { useRunStore } from "../../stores/runStoreContext";
import { resultSummary } from "./AgentDetail";
import { STATE_LABEL, formatUsd, type RoleSpec } from "./model";

const STATE_BOX: Record<AgentState, string> = {
  idle: "border-border",
  running: "border-status-running bg-status-running/10",
  complete: "border-status-complete bg-status-complete/10",
  failed: "border-status-failed bg-status-failed/10",
  blocked: "border-status-blocked bg-status-blocked/10",
  waiting: "border-status-waiting bg-status-waiting/10",
};
const STATE_TEXT: Record<AgentState, string> = {
  idle: "text-muted-foreground",
  running: "text-status-running",
  complete: "text-status-complete",
  failed: "text-status-failed",
  blocked: "text-status-blocked",
  waiting: "text-status-waiting",
};

export interface TeamViewProps {
  specs: readonly RoleSpec[];
  /** Model label per role (from the project's team); roles without one show no model. */
  models?: Readonly<Record<string, string>>;
  selectedRole?: string | null;
  /** Called with the role when its card is clicked (opens the agent detail). */
  onSelectRole: (role: string) => void;
}

/** One card per role: name and role, model, state, and the current card or last summary. */
export function TeamView({ specs, models = {}, selectedRole, onSelectRole }: TeamViewProps) {
  return (
    <ul
      className="m-0 grid list-none grid-cols-1 gap-2 p-0 sm:grid-cols-2 xl:grid-cols-3"
      aria-label="Team"
      data-testid="team-view"
    >
      {specs.map((s) => (
        <li key={s.role}>
          <TeamCard role={s.role} model={models[s.role]} selected={selectedRole === s.role} onOpen={onSelectRole} />
        </li>
      ))}
    </ul>
  );
}

function TeamCard({
  role,
  model,
  selected,
  onOpen,
}: {
  role: string;
  model?: string;
  selected: boolean;
  onOpen: (role: string) => void;
}) {
  const state = useRunStore((s) => s.agents[role]?.state ?? "idle");
  const name = useRunStore((s) => s.names[role]);
  const cost = useRunStore((s) => s.agents[role]?.cost ?? 0);
  const card = useRunStore((s) => {
    const id = s.agents[role]?.card;
    return id ? s.cards[id] : undefined;
  });
  const summary = useRunStore((s) => resultSummary(s, role));
  const doing = card ? `${card.id} · ${card.title}` : summary;
  return (
    <button
      type="button"
      onClick={() => onOpen(role)}
      aria-label={`${name ? `${name}, ${role}` : role}: ${STATE_LABEL[state]}. Show tool calls and result`}
      className={cn(
        "flex h-full w-full cursor-pointer flex-col gap-1 rounded-lg border-2 bg-card px-3 py-2.5 text-left text-card-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        STATE_BOX[state],
        selected && "ring-2 ring-ring",
      )}
      data-testid={`team-card-${role}`}
      data-state={state}
    >
      <span className="flex w-full items-center justify-between gap-2">
        <span className="min-w-0 truncate text-sm">
          <span className="font-semibold">{name ?? role}</span>
          {name && <span className="text-xs text-muted-foreground"> · {role}</span>}
        </span>
        <span className={cn("shrink-0 text-xs font-medium", STATE_TEXT[state])}>{STATE_LABEL[state]}</span>
      </span>
      <span className="truncate text-xs text-muted-foreground">{model ?? "model not set"}</span>
      <span className="line-clamp-2 w-full text-xs" title={doing}>
        {doing || <span className="text-muted-foreground">No work yet.</span>}
      </span>
      <span className="mt-auto text-[11px] text-muted-foreground">{formatUsd(cost)}</span>
    </button>
  );
}
