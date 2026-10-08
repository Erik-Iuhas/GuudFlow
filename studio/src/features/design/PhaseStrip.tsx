import { cn } from "../../lib/utils";
import { usePhases, type PhaseWire } from "./api";

const DOT: Record<PhaseWire["state"], string> = {
  done: "bg-status-complete",
  current: "bg-status-running",
  todo: "bg-muted-foreground/40",
  skipped: "bg-transparent border border-muted-foreground/40",
};

/**
 * Where the project is in its kind's lifecycle (role contracts plan §3): one step per phase,
 * the current one with its owner and the gate that ends it.
 */
export function PhaseStrip({ projectId }: { projectId: string }) {
  const phases = usePhases(projectId);
  const data = phases.data;
  if (!data) return null;
  const current = data.phases.find((p) => p.state === "current");
  return (
    <div className="rounded-lg border border-border bg-card p-3" data-testid="phase-strip">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">{data.kind_title}</span>
        {current && (
          <span className="text-sm">
            <span className="font-semibold">{current.title}</span>
            <span className="text-muted-foreground"> · {ownerLabel(current.owner)} · {current.note || current.gate}</span>
          </span>
        )}
      </div>
      <ol className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0">
        {data.phases.map((p) => (
          <li key={p.id} className={cn("flex items-center gap-1.5 text-sm",
            p.state === "current" ? "font-semibold" : "text-muted-foreground",
            p.state === "skipped" && "line-through decoration-muted-foreground/50")}
            title={[p.gate, p.note].filter(Boolean).join(" — ")}
            data-state={p.state}>
            <span className={cn("inline-block h-2 w-2 rounded-full", DOT[p.state])} aria-hidden />
            {p.title}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ownerLabel(owner: string): string {
  if (owner === "owner") return "you";
  if (owner === "lead") return "the Lead";
  if (owner === "product_manager") return "the project manager";
  return `the ${owner.replace(/_/g, " ")}`;
}
