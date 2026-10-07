import { DiskBanner } from "../features/system/DiskBanner";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { dashboardApi, type DashboardData } from "../api";
import { WorkerCard } from "../components/WorkerCard";
import { Badge, type BadgeProps } from "../components/ui/badge";
import { Card } from "../components/ui/card";
import { Alert, EmptyState, linkClass, Page, SectionTitle, Skeleton, Table, Td, Th } from "../components/ui/form-controls";
import { cn } from "../lib/utils";
import type { AgentConfig } from "../types";

const POLL_MS = 5000;

const OUTCOME_VARIANT: Record<string, BadgeProps["variant"]> = {
  complete: "complete",
  running: "running",
  failed: "failed",
  queued: "waiting",
};

function formatElapsed(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

function TaskProgressChip({ closed, total }: { closed: number; total: number }) {
  if (total === 0) return null;
  return (
    <Badge variant="secondary">
      {closed} / {total} tasks
    </Badge>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function fetchData() {
    try {
      const d = await dashboardApi.get();
      setData(d);
      setError(null);
    } catch (e) {
      // Keep showing the last good data; surface the failure as a banner.
      setError(e instanceof Error ? e.message : String(e));
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
      <Page title="Dashboard">
        <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading dashboard">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      </Page>
    );
  }

  const d = data ?? {
    active_runs: [],
    recent_completions: [],
    pending_approvals: [],
    top_personas: [],
  };

  return (
    <Page title="Dashboard">
      <DiskBanner />
      {error && (
        <Alert variant="error" className="mb-4">
          Could not refresh the dashboard: {error}
        </Alert>
      )}

      {/* ── Active runs ── */}
      <section className="mb-8">
        <SectionTitle>Active runs</SectionTitle>
        {d.active_runs.length === 0 ? (
          <EmptyState title="No active runs.">
            Start one from <Link to="/projects" className={linkClass}>Projects</Link> or the{" "}
            <Link to="/oneshot" className={linkClass}>One-shot</Link> launcher.
          </EmptyState>
        ) : (
          d.active_runs.map((run) => (
            <Card key={run.run_id} className="mb-3 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <Link to={`/runs/${run.run_id}`} className={cn("font-mono", linkClass)}>
                    {run.run_id}
                  </Link>
                  {run.project_id && (
                    <span className="ml-2 text-xs text-muted-foreground">{run.project_id}</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <TaskProgressChip
                    closed={run.task_progress.closed}
                    total={run.task_progress.total}
                  />
                  <span className="text-xs text-muted-foreground">{formatElapsed(run.elapsed_s)}</span>
                </div>
              </div>
              {run.goal && (
                <div className="mt-1 text-xs text-muted-foreground">{run.goal}</div>
              )}
              {run.current_agents.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
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
            </Card>
          ))
        )}
      </section>

      {/* ── Pending approvals ── */}
      {d.pending_approvals.length > 0 && (
        <section className="mb-8">
          <SectionTitle>Pending approvals</SectionTitle>
          {d.pending_approvals.map((a) => (
            <Card key={a.approval_id} className="mb-2 p-4">
              <div className="mb-1 flex items-center justify-between gap-3">
                <span>{a.title}</span>
                <Link to={`/runs/${a.run_id}`} className={cn("text-sm font-medium", linkClass)}>
                  Review →
                </Link>
              </div>
              <Badge variant="waiting">{a.kind}</Badge>
            </Card>
          ))}
        </section>
      )}

      {/* ── Recent completions ── */}
      <section className="mb-8">
        <SectionTitle>Recent completions</SectionTitle>
        {d.recent_completions.length === 0 ? (
          <EmptyState title="No completed runs yet." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Run</Th>
                <Th>Project</Th>
                <Th>Outcome</Th>
                <Th>Duration</Th>
              </tr>
            </thead>
            <tbody>
              {d.recent_completions.map((r) => (
                <tr key={r.run_id}>
                  <Td>
                    <Link to={`/runs/${r.run_id}`} className={cn("font-mono", linkClass)}>
                      {r.run_id}
                    </Link>
                  </Td>
                  <Td>{r.project_id}</Td>
                  <Td>
                    <Badge variant={OUTCOME_VARIANT[r.outcome] ?? "secondary"}>{r.outcome}</Badge>
                  </Td>
                  <Td className="text-xs text-muted-foreground">
                    {r.duration_s != null ? formatElapsed(r.duration_s) : "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </section>

      {/* ── Top personas ── */}
      <section>
        <SectionTitle>Top personas</SectionTitle>
        {d.top_personas.length === 0 ? (
          <EmptyState title="No personas yet.">
            They appear on the <Link to="/roster" className={linkClass}>Roster</Link> after a first run.
          </EmptyState>
        ) : (
          <div className="flex flex-wrap gap-2">
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
    </Page>
  );
}
