import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { dashboardApi, type DashboardData } from "../api";
import { WorkerCard } from "../components/WorkerCard";
import type { AgentConfig } from "../types";

const POLL_MS = 5000;

function formatElapsed(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

function TaskProgressChip({ closed, total }: { closed: number; total: number }) {
  if (total === 0) return null;
  return (
    <span className="tag small">
      {closed} / {total} tasks
    </span>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function fetchData() {
    try {
      const d = await dashboardApi.get();
      setData(d);
    } catch {
      // silently ignore polling errors
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchData();

    function scheduleNext() {
      timerRef.current = setTimeout(() => {
        if (!document.hidden) fetchData();
        scheduleNext();
      }, POLL_MS);
    }

    scheduleNext();

    function onVisibilityChange() {
      if (!document.hidden) fetchData();
    }
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  if (loading) {
    return (
      <div className="page">
        <h1>Dashboard</h1>
        <div className="skeleton-block" style={{ height: 80, marginBottom: 16 }} />
        <div className="skeleton-block" style={{ height: 80, marginBottom: 16 }} />
        <div className="skeleton-block" style={{ height: 80 }} />
      </div>
    );
  }

  const d = data ?? {
    active_runs: [],
    recent_completions: [],
    pending_approvals: [],
    top_personas: [],
  };

  return (
    <div className="page">
      <h1>Dashboard</h1>

      {/* ── Active runs ── */}
      <section style={{ marginBottom: 32 }}>
        <h2>Active runs</h2>
        {d.active_runs.length === 0 ? (
          <p className="muted small">No active runs.</p>
        ) : (
          d.active_runs.map((run) => (
            <div key={run.run_id} className="card" style={{ marginBottom: 12 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <Link to={`/runs/${run.run_id}`} className="mono">
                    {run.run_id}
                  </Link>
                  {run.project_id && (
                    <span className="muted small" style={{ marginLeft: 8 }}>
                      {run.project_id}
                    </span>
                  )}
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <TaskProgressChip
                    closed={run.task_progress.closed}
                    total={run.task_progress.total}
                  />
                  <span className="muted small">{formatElapsed(run.elapsed_s)}</span>
                </div>
              </div>
              {run.goal && (
                <div className="muted small" style={{ marginTop: 4 }}>
                  {run.goal}
                </div>
              )}
              {run.current_agents.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {run.current_agents.map((ag) => {
                    const agentCfg: AgentConfig = {
                      role: ag.role,
                      persona_id: ag.persona_id,
                    };
                    return (
                      <WorkerCard
                        key={ag.role}
                        agent={agentCfg}
                        mode="live"
                        runId={run.run_id}
                      />
                    );
                  })}
                </div>
              )}
            </div>
          ))
        )}
      </section>

      {/* ── Pending approvals ── */}
      {d.pending_approvals.length > 0 && (
        <section style={{ marginBottom: 32 }}>
          <h2>Pending approvals</h2>
          {d.pending_approvals.map((a) => (
            <div key={a.approval_id} className="card" style={{ marginBottom: 8 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span>{a.title}</span>
                <Link to={`/runs/${a.run_id}`} className="primary small">
                  Review →
                </Link>
              </div>
              <span className="tag small muted">{a.kind}</span>
            </div>
          ))}
        </section>
      )}

      {/* ── Recent completions ── */}
      <section style={{ marginBottom: 32 }}>
        <h2>Recent completions</h2>
        {d.recent_completions.length === 0 ? (
          <p className="muted small">No completed runs yet.</p>
        ) : (
          <table className="list">
            <thead>
              <tr>
                <th>Run</th>
                <th>Project</th>
                <th>Outcome</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {d.recent_completions.map((r) => (
                <tr key={r.run_id}>
                  <td>
                    <Link to={`/runs/${r.run_id}`} className="mono">
                      {r.run_id}
                    </Link>
                  </td>
                  <td>{r.project_id}</td>
                  <td>
                    <span className={`tag tag-status-${r.outcome}`}>{r.outcome}</span>
                  </td>
                  <td className="muted small">
                    {r.duration_s != null ? formatElapsed(r.duration_s) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* ── Top personas ── */}
      <section>
        <h2>Top personas</h2>
        {d.top_personas.length === 0 ? (
          <p className="muted small">No personas yet.</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {d.top_personas.map((p) => {
              const agentCfg: AgentConfig = {
                role: p.role || p.name,
                persona_id: p.persona_id,
              };
              return (
                <WorkerCard
                  key={p.persona_id}
                  agent={agentCfg}
                  mode="roster"
                />
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
