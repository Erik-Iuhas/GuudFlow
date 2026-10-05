import { useEffect, useRef, useState } from "react";
import type { Task } from "../types";
import { useDesks } from "../hooks/useDesks";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";

interface Props {
  runId: string;
  role: string;
}

function currentTask(tasks: Task[]): Task | null {
  return (
    tasks.find((t) => t.status === "IN_PROGRESS") ??
    tasks.find((t) => t.status === "PENDING") ??
    null
  );
}

function truncate(s: string, n = 60): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

export function ActiveTaskChip({ runId, role }: Props) {
  const { data } = useDesks(runId);
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  const tasks = data?.desks[role] ?? [];
  const task = currentTask(tasks);

  // Close popover on outside click
  useEffect(() => {
    if (!open) return;
    function handler(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (!task) {
    return <Badge variant="secondary" className="text-xs text-muted-foreground">idle</Badge>;
  }

  const label = task.status === "IN_PROGRESS" ? "in progress" : "pending";

  return (
    <span className="relative inline-block">
      <button
        type="button"
        className={cn(
          "cursor-pointer rounded-full border px-2 py-0.5 text-xs font-medium leading-4",
          label === "in progress"
            ? "border-status-running/30 bg-status-running/15 text-status-running"
            : "border-border bg-secondary text-secondary-foreground",
        )}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        title="Click to see task details"
      >
        {truncate(task.goal)}
      </button>
      {open && (
        <div
          ref={popoverRef}
          className="absolute left-0 top-full z-[100] mt-1 min-w-[280px] max-w-[400px] rounded-md border border-border bg-popover p-3 text-popover-foreground shadow-lg"
        >
          <div className="mb-1.5 text-xs font-semibold">{task.goal}</div>
          {task.acceptance_criteria.length > 0 && (
            <div>
              <div className="mb-0.5 text-xs text-muted-foreground">Acceptance criteria</div>
              <ul className="m-0 pl-4 text-xs">
                {task.acceptance_criteria.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
          {task.references.length > 0 && (
            <div className="mt-1.5">
              <div className="mb-0.5 text-xs text-muted-foreground">References</div>
              {task.references.map((r, i) => (
                <div key={i} className="text-xs">
                  <Badge variant="secondary">{r.kind}</Badge> {r.value}
                  {r.note && <span className="text-muted-foreground"> — {r.note}</span>}
                </div>
              ))}
            </div>
          )}
          {task.closed_by_commit && (
            <div className="mt-1.5 text-xs text-muted-foreground">
              closes: <span className="font-mono">{task.closed_by_commit.slice(0, 8)}</span>
            </div>
          )}
        </div>
      )}
    </span>
  );
}
