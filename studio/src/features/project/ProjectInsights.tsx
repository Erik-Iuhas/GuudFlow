import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { usePlaybook, useRunsMetrics, type PlaybookEntryWire, type RunMetricsPoint } from "../report/api";
import { Sparkline } from "./Sparkline";
import { SERIES, groupPlaybook, seriesValues, sparklineSummary, valueText } from "./model";

function stateVariant(state: string): BadgeProps["variant"] {
  if (state === "active") return "complete";
  if (state === "emerging") return "running";
  if (state === "declining") return "blocked";
  return "outline";
}

/** Overseer metrics across runs: one labelled sparkline and a text line per series. */
export function MetricsTrend({ runs }: { runs: readonly RunMetricsPoint[] }) {
  if (runs.length === 0) {
    return <p className="m-0 text-sm text-muted-foreground">No finished run has recorded metrics for this project yet.</p>;
  }
  const latest = runs[runs.length - 1] as RunMetricsPoint;
  return (
    <div className="flex flex-col gap-3">
      <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 lg:grid-cols-3" aria-label="Overseer metrics by run">
        {SERIES.map((s) => {
          const points = seriesValues(runs, s.key);
          const values = points.map((p) => p.value);
          return (
            <li key={s.key} className="flex flex-col gap-1 rounded-lg border border-border bg-card p-3" data-testid="metric">
              <span className="text-sm font-medium">{s.label}</span>
              <Sparkline label={s.label} values={values} />
              <span className="text-xs text-muted-foreground">{sparklineSummary(s.label, values)}</span>
              <span className="text-xs text-muted-foreground">{s.better === "lower" ? "Lower is better." : "Higher is better."}</span>
            </li>
          );
        })}
      </ul>
      <p className="m-0 text-xs text-muted-foreground">
        Oldest run on the left. Latest run:{" "}
        <Link to={`/runs/${encodeURIComponent(latest.run_id)}/report`}>{latest.run_id}</Link>.
      </p>
    </div>
  );
}

function Confidence({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <span className="flex items-center gap-2">
      <svg viewBox="0 0 100 6" className="h-1.5 w-20" role="img" aria-label={`confidence ${pct}%`}>
        <rect width="100" height="6" rx="2" className="fill-muted" />
        <rect width={pct} height="6" rx="2" className="fill-primary" />
      </svg>
      <span className="text-xs tabular-nums text-muted-foreground">{pct}%</span>
    </span>
  );
}

function EntryRow({ entry }: { entry: PlaybookEntryWire }) {
  return (
    <li className="flex flex-col gap-1 rounded-md border border-border bg-card p-3 text-sm" data-testid="playbook-entry">
      <div className="flex flex-wrap items-center gap-2">
        {entry.key && <code className="text-xs">{entry.key}</code>}
        <Badge variant={stateVariant(entry.state)}>{entry.state}</Badge>
        {entry.needs_review && <Badge variant="blocked">needs review</Badge>}
        <Confidence value={entry.confidence} />
      </div>
      <p className="m-0">{valueText(entry.value)}</p>
      <p className="m-0 text-xs text-muted-foreground">
        Evidence:{" "}
        {entry.evidence_runs.map((r, i) => (
          <span key={r}>
            {i > 0 && ", "}
            <Link to={`/runs/${encodeURIComponent(r)}/report`}>{r}</Link>
          </span>
        ))}
      </p>
    </li>
  );
}

/** The playbook grouped by section. */
export function PlaybookView({ entries }: { entries: readonly PlaybookEntryWire[] }) {
  const groups = useMemo(() => groupPlaybook(entries), [entries]);
  if (groups.length === 0) {
    return <p className="m-0 text-sm text-muted-foreground">The playbook is empty. Lessons appear after runs finish.</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => (
        <section key={g.section} aria-label={g.label} data-testid={`playbook-${g.section}`} className="flex flex-col gap-2">
          <h3 className="m-0 text-sm font-semibold">{g.label} ({g.entries.length})</h3>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {g.entries.map((e) => <EntryRow key={e.id} entry={e} />)}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** Project page section: overseer metrics trending across runs, and the playbook. */
export function ProjectInsights({ projectId }: { projectId: string }) {
  const metrics = useRunsMetrics(projectId);
  const playbook = usePlaybook(projectId);
  return (
    <div className="flex flex-col gap-6" data-testid="project-insights">
      <section aria-labelledby="insights-metrics" className="flex flex-col gap-2">
        <h2 id="insights-metrics" className="m-0 text-base font-semibold">Is the overseer helping?</h2>
        {metrics.isPending && <p role="status" className="m-0 text-sm text-muted-foreground">Loading metrics…</p>}
        {metrics.isError && <p role="alert" className="m-0 text-sm text-destructive">Could not load metrics: {metrics.error.message}</p>}
        {metrics.data && <MetricsTrend runs={metrics.data} />}
      </section>
      <section aria-labelledby="insights-playbook" className="flex flex-col gap-2">
        <h2 id="insights-playbook" className="m-0 text-base font-semibold">Playbook</h2>
        {playbook.isPending && <p role="status" className="m-0 text-sm text-muted-foreground">Loading playbook…</p>}
        {playbook.isError && <p role="alert" className="m-0 text-sm text-destructive">Could not load the playbook: {playbook.error.message}</p>}
        {playbook.data && <PlaybookView entries={playbook.data} />}
      </section>
    </div>
  );
}
