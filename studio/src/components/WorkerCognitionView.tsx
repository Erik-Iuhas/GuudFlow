// Track 11 — Workers tab content. Polls /api/runs/{id}/workers ~1.5s
// while the tab is mounted and renders one card per agent role with
// the agent's current state of thinking: active subtask, in-flight
// tool call, last result, iteration cost, subtask history.

import { useEffect, useRef, useState } from "react";
import { runsApi, type WorkersSnapshot, type WorkerCognition } from "../api";
import { errorCls } from "../lib/field";
import { cn } from "../lib/utils";
import { Badge, type BadgeProps } from "./ui/badge";
import { Card } from "./ui/card";

const POLL_INTERVAL_MS = 1500;

interface Props {
  runId: string;
  isActive: boolean;
}

function fmtCost(usd: number): string {
  if (!usd || usd <= 0) return "—";
  if (usd < 0.01) return "<$0.01";
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

function fmtSeconds(s: number): string {
  if (s < 60) return `${s.toFixed(1)}s`;
  const mins = Math.floor(s / 60);
  return `${mins}m ${(s - mins * 60).toFixed(0)}s`;
}

const STATE_VARIANTS: Record<string, BadgeProps["variant"]> = {
  running: "complete",
  complete: "secondary",
  pending: "secondary",
  failed: "failed",
  waiting_for_human: "blocked",
  waiting_for_peer: "waiting",
  waiting_for_skip_confirm: "waiting",
  sat_out: "secondary",
};

const SUBTASK_TONE: Record<string, string> = {
  COMPLETE: "bg-status-complete/15",
  IN_PROGRESS: "bg-status-running/15",
  BLOCKED: "bg-status-blocked/15",
};

const LABEL_CLS = "mb-0.5 block text-xs text-muted-foreground";

function WorkerCard({ w }: { w: WorkerCognition }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  return (
    <Card className="mb-3 p-3.5">
      {/* Header: role + state pill + iteration cost */}
      <div className="mb-2.5 flex items-center gap-2.5">
        <code className="text-sm font-semibold">{w.role}</code>
        <Badge variant={STATE_VARIANTS[w.state] ?? "secondary"} className="uppercase tracking-wide">
          {w.state}
        </Badge>
        <span className="ml-auto text-xs text-muted-foreground">
          iter {w.iteration} · {fmtCost(w.iteration_cost_usd)}
        </span>
      </div>

      {/* Active subtask */}
      <div className="mb-2">
        <span className={LABEL_CLS}>Subtask</span>
        {w.active_subtask ? (
          <div>
            <div className="text-[13px]">{w.active_subtask.goal}</div>
            <div className="mt-0.5 font-mono text-xs text-muted-foreground">
              {w.active_subtask.id}
              {w.active_subtask.parent_task_id && (
                <> · parent {w.active_subtask.parent_task_id}</>
              )}
            </div>
            {w.active_subtask.acceptance.length > 0 && (
              <ul className="mb-0 mt-1 pl-[18px] text-xs text-muted-foreground">
                {w.active_subtask.acceptance.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">— working against project goal directly —</span>
        )}
      </div>

      {/* Tool now / Last tool */}
      <div className="mb-2 grid grid-cols-2 gap-3">
        <div>
          <span className={LABEL_CLS}>Tool now</span>
          {w.current_tool_call ? (
            <div>
              <code className="text-xs">{w.current_tool_call.name}</code>
              <span className="ml-1.5 text-xs text-muted-foreground">
                {fmtSeconds(w.current_tool_call.elapsed_seconds)}
              </span>
              <div
                className="mt-0.5 truncate font-mono text-xs text-muted-foreground"
                title={w.current_tool_call.arguments_preview}
              >
                {w.current_tool_call.arguments_preview}
              </div>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">— thinking —</span>
          )}
        </div>
        <div>
          <span className={LABEL_CLS}>Last tool</span>
          {w.last_tool_summary ? (
            <div>
              <code className="text-xs">{w.last_tool_summary.name}</code>
              <span className="ml-1.5 text-xs text-muted-foreground">
                {fmtSeconds(w.last_tool_summary.ago_seconds)} ago
              </span>
              <div
                className="mt-0.5 truncate text-xs text-muted-foreground"
                title={w.last_tool_summary.summary}
              >
                → {w.last_tool_summary.summary}
              </div>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          )}
        </div>
      </div>

      {/* Subtask history */}
      {w.subtask_history.length > 0 && (
        <div className="mt-2 border-t border-border pt-1.5">
          <button
            type="button"
            onClick={() => setHistoryOpen((v) => !v)}
            className="cursor-pointer border-0 bg-transparent p-0 text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
          >
            {historyOpen ? "▾" : "▸"} subtask history ({w.subtask_history.length})
          </button>
          {historyOpen && (
            <ul className="mb-0 mt-1.5 pl-[18px] text-xs text-muted-foreground">
              {w.subtask_history.map((t) => (
                <li key={t.id} className="mb-0.5">
                  <span
                    className={cn(
                      "mr-1.5 rounded-sm bg-muted px-[5px] py-px text-[10px] text-foreground",
                      SUBTASK_TONE[t.status],
                    )}
                  >
                    {t.status}
                  </span>
                  {t.goal}
                  <span className="ml-1.5 font-mono text-xs text-muted-foreground">
                    {t.id.slice(0, 8)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

export function WorkerCognitionView({ runId, isActive }: Props) {
  const [snap, setSnap] = useState<WorkersSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    let timer: number | null = null;

    const poll = async () => {
      try {
        const next = await runsApi.workers(runId);
        if (aliveRef.current) {
          setSnap(next);
          setError(null);
        }
      } catch (e) {
        if (aliveRef.current) setError(e instanceof Error ? e.message : String(e));
      }
      if (aliveRef.current && isActive) {
        timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    void poll();
    return () => {
      aliveRef.current = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [runId, isActive]);

  if (error && !snap) {
    return <div className={errorCls}>Failed to load workers: {error}</div>;
  }

  if (!snap) {
    return <p className="text-xs text-muted-foreground">Loading workers…</p>;
  }

  return (
    <div className="px-4 py-3">
      {!snap.live && (
        <div className="mb-3 rounded-md border border-border bg-muted px-3 py-2 text-xs text-muted-foreground">
          Run is no longer active — showing the final task layout from disk.
          Live cognition fields are unavailable for completed runs.
        </div>
      )}
      {snap.workers.length === 0 && (
        <p className="text-xs text-muted-foreground">No worker activity yet.</p>
      )}
      {snap.workers.map((w) => (
        <WorkerCard key={w.role} w={w} />
      ))}
    </div>
  );
}
