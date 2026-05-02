// Track 11 — Workers tab content. Polls /api/runs/{id}/workers ~1.5s
// while the tab is mounted and renders one card per agent role with
// the agent's current state of thinking: active subtask, in-flight
// tool call, last result, iteration cost, subtask history.

import { useEffect, useRef, useState } from "react";
import { runsApi, type WorkersSnapshot, type WorkerCognition } from "../api";

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

const STATE_COLORS: Record<string, string> = {
  running: "#16a34a",
  complete: "#64748b",
  pending: "#94a3b8",
  failed: "#dc2626",
  waiting_for_human: "#f59e0b",
  waiting_for_peer: "#a855f7",
  waiting_for_skip_confirm: "#a855f7",
  sat_out: "#94a3b8",
};

function WorkerCard({ w }: { w: WorkerCognition }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const stateColor = STATE_COLORS[w.state] ?? "#64748b";
  return (
    <div
      style={{
        border: "1px solid #e2e8f0",
        borderRadius: 8,
        padding: 14,
        background: "#fff",
        marginBottom: 12,
      }}
    >
      {/* Header: role + state pill + iteration cost */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <code style={{ fontWeight: 600, fontSize: 14 }}>{w.role}</code>
        <span
          style={{
            fontSize: 11,
            padding: "2px 8px",
            borderRadius: 12,
            background: stateColor,
            color: "#fff",
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {w.state}
        </span>
        <span className="muted small" style={{ marginLeft: "auto" }}>
          iter {w.iteration} · {fmtCost(w.iteration_cost_usd)}
        </span>
      </div>

      {/* Active subtask */}
      <div style={{ marginBottom: 8 }}>
        <span className="muted small" style={{ display: "block", marginBottom: 2 }}>
          Subtask
        </span>
        {w.active_subtask ? (
          <div>
            <div style={{ fontSize: 13 }}>{w.active_subtask.goal}</div>
            <div className="muted small" style={{ fontFamily: "monospace", marginTop: 2 }}>
              {w.active_subtask.id}
              {w.active_subtask.parent_task_id && (
                <> · parent {w.active_subtask.parent_task_id}</>
              )}
            </div>
            {w.active_subtask.acceptance.length > 0 && (
              <ul style={{ marginTop: 4, marginBottom: 0, paddingLeft: 18, fontSize: 12, color: "#475569" }}>
                {w.active_subtask.acceptance.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <span className="muted small">— working against project goal directly —</span>
        )}
      </div>

      {/* Tool now / Last tool */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 8 }}>
        <div>
          <span className="muted small" style={{ display: "block", marginBottom: 2 }}>
            Tool now
          </span>
          {w.current_tool_call ? (
            <div>
              <code style={{ fontSize: 12 }}>{w.current_tool_call.name}</code>
              <span className="muted small" style={{ marginLeft: 6 }}>
                {fmtSeconds(w.current_tool_call.elapsed_seconds)}
              </span>
              <div
                className="muted small"
                style={{
                  fontFamily: "monospace",
                  marginTop: 2,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
                title={w.current_tool_call.arguments_preview}
              >
                {w.current_tool_call.arguments_preview}
              </div>
            </div>
          ) : (
            <span className="muted small">— thinking —</span>
          )}
        </div>
        <div>
          <span className="muted small" style={{ display: "block", marginBottom: 2 }}>
            Last tool
          </span>
          {w.last_tool_summary ? (
            <div>
              <code style={{ fontSize: 12 }}>{w.last_tool_summary.name}</code>
              <span className="muted small" style={{ marginLeft: 6 }}>
                {fmtSeconds(w.last_tool_summary.ago_seconds)} ago
              </span>
              <div
                className="muted small"
                style={{
                  marginTop: 2,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
                title={w.last_tool_summary.summary}
              >
                → {w.last_tool_summary.summary}
              </div>
            </div>
          ) : (
            <span className="muted small">—</span>
          )}
        </div>
      </div>

      {/* Subtask history */}
      {w.subtask_history.length > 0 && (
        <div style={{ marginTop: 8, borderTop: "1px solid #f1f5f9", paddingTop: 6 }}>
          <button
            onClick={() => setHistoryOpen((v) => !v)}
            style={{
              border: "none",
              background: "none",
              padding: 0,
              cursor: "pointer",
              color: "#64748b",
              fontSize: 11,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              fontWeight: 500,
            }}
          >
            {historyOpen ? "▾" : "▸"} subtask history ({w.subtask_history.length})
          </button>
          {historyOpen && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12, color: "#475569" }}>
              {w.subtask_history.map((t) => (
                <li key={t.id} style={{ marginBottom: 2 }}>
                  <span
                    style={{
                      fontSize: 10,
                      padding: "1px 5px",
                      borderRadius: 3,
                      marginRight: 6,
                      background: t.status === "COMPLETE" ? "#dcfce7"
                        : t.status === "IN_PROGRESS" ? "#dbeafe"
                        : t.status === "BLOCKED" ? "#fef3c7"
                        : "#f1f5f9",
                      color: "#334155",
                    }}
                  >
                    {t.status}
                  </span>
                  {t.goal}
                  <span className="muted small" style={{ fontFamily: "monospace", marginLeft: 6 }}>
                    {t.id.slice(0, 8)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
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
    return <div className="error">Failed to load workers: {error}</div>;
  }

  if (!snap) {
    return <p className="muted small">Loading workers…</p>;
  }

  return (
    <div style={{ padding: "12px 16px" }}>
      {!snap.live && (
        <div
          style={{
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: 6,
            padding: "8px 12px",
            marginBottom: 12,
            fontSize: 12,
            color: "#64748b",
          }}
        >
          Run is no longer active — showing the final task layout from disk.
          Live cognition fields are unavailable for completed runs.
        </div>
      )}
      {snap.workers.length === 0 && (
        <p className="muted small">No worker activity yet.</p>
      )}
      {snap.workers.map((w) => (
        <WorkerCard key={w.role} w={w} />
      ))}
    </div>
  );
}
