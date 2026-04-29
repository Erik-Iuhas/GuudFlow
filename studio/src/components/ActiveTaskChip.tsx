import { useEffect, useRef, useState } from "react";
import type { Task } from "../types";
import { useDesks } from "../hooks/useDesks";

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
    return <span className="tag small muted">idle</span>;
  }

  const label = task.status === "IN_PROGRESS" ? "in progress" : "pending";

  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <button
        className={`tag small tag-task-${label.replace(" ", "-")}`}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        title="Click to see task details"
        style={{ cursor: "pointer" }}
      >
        {truncate(task.goal)}
      </button>
      {open && (
        <div
          ref={popoverRef}
          className="task-popover"
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            zIndex: 100,
            minWidth: 280,
            maxWidth: 400,
            background: "#fff",
            border: "1px solid #e2e8f0",
            borderRadius: 6,
            padding: 12,
            boxShadow: "0 4px 16px rgba(0,0,0,.1)",
            marginTop: 4,
          }}
        >
          <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
            {task.goal}
          </div>
          {task.acceptance_criteria.length > 0 && (
            <div>
              <div className="small muted" style={{ marginBottom: 2 }}>Acceptance criteria</div>
              <ul className="small" style={{ margin: 0, paddingLeft: 16 }}>
                {task.acceptance_criteria.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
          {task.references.length > 0 && (
            <div style={{ marginTop: 6 }}>
              <div className="small muted" style={{ marginBottom: 2 }}>References</div>
              {task.references.map((r, i) => (
                <div key={i} className="small">
                  <span className="tag">{r.kind}</span> {r.value}
                  {r.note && <span className="muted"> — {r.note}</span>}
                </div>
              ))}
            </div>
          )}
          {task.closed_by_commit && (
            <div className="small muted" style={{ marginTop: 6 }}>
              closes: <span className="mono">{task.closed_by_commit.slice(0, 8)}</span>
            </div>
          )}
        </div>
      )}
    </span>
  );
}
