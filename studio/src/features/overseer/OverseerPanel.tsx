import { useMemo } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { useRunStore } from "../../stores/runStoreContext";
import { interventionTarget } from "../cockpit/model";
import { useRunReport } from "../report/api";
import { buildWakes, interventionLabel, type WakeIntervention } from "./model";

function focusApprovals(): void {
  document.querySelector<HTMLElement>("[data-approvals] button")?.focus();
}

function Outcome({ worked }: { worked: boolean | null }) {
  if (worked === null) return null;
  return (
    <Badge variant={worked ? "complete" : "failed"} data-testid="outcome">
      {worked ? "worked: target recovered" : "did not work: target still open"}
    </Badge>
  );
}

function InterventionRow({ iv }: { iv: WakeIntervention }) {
  const d = iv.data;
  return (
    <li data-testid="intervention" className="rounded-md border border-border bg-card p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="waiting">{interventionLabel(d.type)}</Badge>
        <span className="text-xs text-muted-foreground">{interventionTarget(d)}</span>
        {d.needs_human && <Badge variant="blocked">needs human</Badge>}
        <Outcome worked={iv.worked} />
      </div>
      <p className="m-0 mt-1">{d.rationale}</p>
      {d.addendum && <p className="m-0 mt-1 text-xs text-muted-foreground">Brief: {d.addendum}</p>}
      {d.question && <p className="m-0 mt-1 text-xs">Question: {d.question}</p>}
      {d.decision && <p className="m-0 mt-1 text-xs">Decision: {d.decision}</p>}
      {d.summary && <p className="m-0 mt-1 text-xs text-muted-foreground">{d.summary}</p>}
    </li>
  );
}

/**
 * The overseer timeline: each wake with the detectors that fired, its assessment and the
 * interventions it made (type, target, rationale, needs-human, and whether it worked once
 * the run has ended). Must sit under a `RunStoreProvider`.
 */
export function OverseerPanel() {
  const signals = useRunStore((s) => s.signals);
  const live = useRunStore((s) => s.live);
  const runId = useRunStore((s) => s.runId);
  const approvals = useRunStore((s) => s.approvals);
  const humanNeeded = useRunStore((s) => s.humanNeeded);

  // Outcomes exist only after the end-of-run retrospective.
  const report = useRunReport(runId, !live);
  const outcomes = useMemo(() => {
    const map = new Map<string, boolean | null>();
    for (const iv of report.data?.interventions ?? []) map.set(iv.signal_id, iv.worked);
    return map;
  }, [report.data]);
  const wakes = useMemo(() => buildWakes(signals, outcomes), [signals, outcomes]);

  const pending = approvals.filter((a) => a.kind === "team_change" || a.kind === "pause");
  const overseerAsk = humanNeeded?.role === "overseer" ? humanNeeded : null;

  return (
    <div className="flex flex-col gap-2 p-2" data-testid="overseer-panel">
      {(pending.length > 0 || overseerAsk) && (
        <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border border-status-waiting/40 bg-status-waiting/10 p-2 text-sm">
          {pending.map((a) => (
            <span key={a.id} className="flex items-center gap-2">
              <Badge variant="blocked">{a.kind.replace("_", " ")}</Badge>
              <span>{a.summary}</span>
              <Button size="sm" variant="outline" onClick={focusApprovals}>
                Go to approvals bar
              </Button>
            </span>
          ))}
          {overseerAsk && <span>The overseer paused the run and asks: {overseerAsk.summary}. Answer in the comment box.</span>}
        </div>
      )}
      {wakes.length === 0 ? (
        <p className="m-0 p-1 text-sm text-muted-foreground">
          No wakes. The overseer has not stepped in on this run.
        </p>
      ) : (
        <ol aria-label="Overseer wakes" className="m-0 flex list-none flex-col gap-3 p-0">
          {[...wakes].reverse().map((w, i) => (
            <li key={w.id} data-testid="wake" className="flex flex-col gap-2">
              <div className="flex flex-wrap items-baseline gap-2">
                <h3 className="m-0 text-sm font-semibold">Wake {wakes.length - i}</h3>
                <time className="text-xs text-muted-foreground" dateTime={w.timestamp}>
                  {new Date(w.timestamp).toLocaleTimeString()}
                </time>
              </div>
              {w.stalls.length > 0 && (
                <ul aria-label="Detectors that fired" className="m-0 flex list-none flex-wrap gap-1 p-0">
                  {w.stalls.map((s, k) => (
                    <li key={k}>
                      <Badge variant="outline" data-testid="stall" title={s}>
                        {s.length > 80 ? `${s.slice(0, 80)}…` : s}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
              <p className="m-0 text-sm text-muted-foreground" data-testid="assessment">{w.assessment}</p>
              {w.interventions.length === 0 ? (
                <p className="m-0 text-xs text-muted-foreground">No intervention: it judged the run fine.</p>
              ) : (
                <ol aria-label="Interventions" className="m-0 flex list-none flex-col gap-2 p-0">
                  {w.interventions.map((iv) => <InterventionRow key={iv.signalId} iv={iv} />)}
                </ol>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
