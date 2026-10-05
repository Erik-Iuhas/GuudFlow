import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { getCards } from "../../api";
import { Badge } from "../../components/ui/badge";
import { useRunSocket } from "../../hooks/useRunSocket";
import { statusVariant } from "../../lib/runStatus";
import { createRunStore } from "../../stores/runEvents";
import { RunStoreProvider, useRunStore } from "../../stores/runStoreContext";
import { useArtifactAccess } from "../artifacts/access";
import { buildItems, isImageItem } from "../artifacts/model";
import { interventionLabel } from "../overseer/model";
import { useRunReport, type BranchRow, type RunReportData } from "./api";
import { CommitDiff } from "./DiffViewer";
import { costBars, formatSpan, formatTokens, formatUsd } from "./model";

function Section({ title, id, children }: { title: string; id: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4" data-testid={`section-${id}`}>
      <h2 id={id} className="m-0 text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="m-0 text-sm text-muted-foreground">{children}</p>;
}

const TH = "px-2 py-1 text-left text-xs font-medium text-muted-foreground";
const TD = "px-2 py-1 align-top";
const NUM = "px-2 py-1 text-right tabular-nums";

function Summary({ report }: { report: RunReportData }) {
  const retro = report.retrospective;
  const done = retro ? retro.cards.filter((c) => c.column === "done").length : null;
  const status = report.meta.status ?? "unknown";
  const span = formatSpan(report.first_signal_at, report.last_signal_at);
  const stat = (label: string, value: ReactNode) => (
    <div className="flex flex-col">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="m-0 text-sm font-medium">{value}</dd>
    </div>
  );
  return (
    <Section title="Summary" id="summary">
      <dl className="m-0 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {stat("Status", <Badge variant={statusVariant(status)}>{status}</Badge>)}
        {stat("Duration", span ?? "unknown")}
        {stat("Cards done", retro ? `${done} / ${retro.cards.length}` : "not available yet")}
        {stat("Stall time", retro ? `${retro.stall_minutes.toFixed(1)} min` : "not available yet")}
        {stat("Cost", formatUsd(report.costs.total.cost_usd))}
      </dl>
      {!retro && <Empty>The retrospective is written when the run ends; cards and stall time appear then.</Empty>}
    </Section>
  );
}

function CostSection({ report }: { report: RunReportData }) {
  const bars = useMemo(() => costBars(report.costs.rows), [report.costs.rows]);
  const total = report.costs.total;
  return (
    <Section title="Cost per agent" id="cost">
      {bars.length === 0 ? (
        <Empty>No model calls were recorded for this run.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm" data-testid="cost-table">
            <thead>
              <tr>
                <th className={TH}>Agent</th>
                <th className={`${TH} text-right`}>Calls</th>
                <th className={`${TH} text-right`}>Input</th>
                <th className={`${TH} text-right`}>Output</th>
                <th className={`${TH} text-right`}>Cache</th>
                <th className={`${TH} text-right`}>Images</th>
                <th className={`${TH} text-right`}>Cost</th>
                <th className={TH}>Tokens (input + output)</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((r) => (
                <tr key={r.role} className="border-t border-border" data-testid="cost-row">
                  <th scope="row" className={`${TD} text-left font-medium`}>{r.role}</th>
                  <td className={NUM}>{r.calls}</td>
                  <td className={NUM}>{formatTokens(r.input_tokens)}</td>
                  <td className={NUM}>{formatTokens(r.output_tokens)}</td>
                  <td className={NUM}>{formatTokens(r.cache_read_tokens)}</td>
                  <td className={NUM}>{r.images}</td>
                  <td className={NUM}>{formatUsd(r.cost_usd)}</td>
                  <td className="w-40 px-2 py-1">
                    <svg viewBox="0 0 100 8" className="h-2 w-full" role="img" aria-label={`${r.role}: ${formatTokens(r.tokens)} tokens`}>
                      <rect width="100" height="8" rx="2" className="fill-muted" />
                      <rect width={r.pct} height="8" rx="2" className="fill-primary" />
                    </svg>
                    <span className="text-xs text-muted-foreground">{formatTokens(r.tokens)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-border font-medium">
                <th scope="row" className={`${TD} text-left`}>Total</th>
                <td className={NUM}>{total.calls}</td>
                <td className={NUM}>{formatTokens(total.input_tokens)}</td>
                <td className={NUM}>{formatTokens(total.output_tokens)}</td>
                <td className={NUM}>{formatTokens(total.cache_read_tokens)}</td>
                <td className={NUM}>{total.images}</td>
                <td className={NUM}>{formatUsd(total.cost_usd)}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </Section>
  );
}

function BranchBlock({ runId, branch }: { runId: string; branch: BranchRow }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-1" data-testid="branch">
      <h3 className="m-0 font-mono text-sm font-semibold">{branch.branch}</h3>
      {branch.commits.length === 0 ? (
        <Empty>No commits on this branch.</Empty>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {branch.commits.map((c) => (
            <li key={c.sha} className="rounded-md border border-border">
              <button
                type="button"
                aria-expanded={open === c.sha}
                onClick={() => setOpen(open === c.sha ? null : c.sha)}
                className="flex w-full items-baseline gap-2 bg-transparent px-2 py-1 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <code className="text-xs text-primary">{c.short_sha}</code>
                <span className="min-w-0 flex-1 truncate">{c.subject}</span>
                <span className="text-xs text-muted-foreground">{open === c.sha ? "Hide diff" : "Show diff"}</span>
              </button>
              {open === c.sha && (
                <div className="border-t border-border">
                  <CommitDiff runId={runId} sha={c.sha} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BranchesSection({ report }: { report: RunReportData }) {
  return (
    <Section title="Branches and commits" id="branches">
      {report.branches.length === 0 ? (
        <Empty>No agent branches were found in this run's repository.</Empty>
      ) : (
        report.branches.map((b) => <BranchBlock key={b.branch} runId={report.run_id} branch={b} />)
      )}
    </Section>
  );
}

/** A strip of the run's screenshots and images; the full gallery is one link away. */
function ScreenshotStrip({ runId }: { runId: string }) {
  const artifacts = useRunStore((s) => s.artifacts);
  const items = useMemo(() => buildItems(artifacts, runId).filter(isImageItem), [artifacts, runId]);
  const ready = useArtifactAccess(runId);
  return (
    <Section title="Screenshots" id="screenshots">
      {!ready && items.length > 0 ? (
        <Empty>Loading images...</Empty>
      ) : items.length === 0 ? (
        <Empty>No screenshots or images in this run.</Empty>
      ) : (
        <ul className="m-0 flex list-none gap-2 overflow-x-auto p-0" data-testid="screenshot-strip">
          {items.slice(0, 24).map((i) => (
            <li key={i.id} className="shrink-0">
              <Link to={`/runs/${encodeURIComponent(runId)}/artifacts`}>
                <img src={i.url} alt={`${i.name}${i.role ? ` by ${i.role}` : ""}`} loading="lazy" className="h-24 w-auto rounded-md border border-border" />
              </Link>
            </li>
          ))}
        </ul>
      )}
      <Link to={`/runs/${encodeURIComponent(runId)}/artifacts`} className="text-sm">Open the full artifact gallery</Link>
    </Section>
  );
}

function WebSection({ report }: { report: RunReportData }) {
  return (
    <Section title="Web requests" id="web">
      {report.web.length === 0 ? (
        <Empty>No agent used the web in this run.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm" data-testid="web-table">
            <thead>
              <tr>
                <th className={TH}>Agent</th><th className={TH}>Tool</th><th className={TH}>Query or URL</th>
                <th className={TH}>Status</th><th className={`${TH} text-right`}>Bytes</th><th className={TH}>Notes</th>
              </tr>
            </thead>
            <tbody>
              {report.web.map((w, i) => (
                <tr key={i} className="border-t border-border">
                  <td className={TD}>{w.role ?? "?"}</td>
                  <td className={TD}>{w.tool}</td>
                  <td className={`${TD} break-all`}>{w.query ?? w.url ?? ""}</td>
                  <td className={TD}>{w.status ?? ""}</td>
                  <td className={NUM}>{w.bytes ?? 0}</td>
                  <td className={TD}>
                    {w.refused ? <Badge variant="failed">refused: {w.refused}</Badge> : w.cache_hit ? <Badge variant="outline">cache hit</Badge> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function CapabilitiesSection({ report }: { report: RunReportData }) {
  return (
    <Section title="Capabilities per agent" id="capabilities">
      {report.capabilities.length === 0 ? (
        <Empty>No capability record on the board.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm" data-testid="capability-table">
            <thead>
              <tr><th className={TH}>Agent</th><th className={TH}>Type</th><th className={TH}>Model</th><th className={TH}>Effective capabilities</th></tr>
            </thead>
            <tbody>
              {report.capabilities.map((c) => (
                <tr key={c.role} className="border-t border-border">
                  <th scope="row" className={`${TD} text-left font-medium`}>{c.role}</th>
                  <td className={TD}>{c.role_type ?? ""}</td>
                  <td className={`${TD} font-mono text-xs`}>{c.model ?? ""}</td>
                  <td className={TD}>
                    <ul className="m-0 flex list-none flex-wrap gap-1 p-0">
                      {Object.entries(c.effective ?? {}).map(([k, v]) => (
                        <li key={k}><Badge variant="outline">{k}: {v}</Badge></li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function RetrospectiveSection({ report }: { report: RunReportData }) {
  const retro = report.retrospective;
  return (
    <Section title="Retrospective" id="retrospective">
      {!retro ? (
        <Empty>No retrospective yet. It is written after the run ends.</Empty>
      ) : (
        <>
          <h3 className="m-0 text-sm font-semibold">Recurring review issues</h3>
          {retro.recurring_issues.length === 0 ? (
            <Empty>No review issue came back twice.</Empty>
          ) : (
            <ul className="m-0 pl-5 text-sm">
              {retro.recurring_issues.map(([card, issue], i) => <li key={i}><code>{card}</code> {issue}</li>)}
            </ul>
          )}
          {Object.keys(retro.review_rounds_used).length > 0 && (
            <>
              <h3 className="m-0 text-sm font-semibold">Review rounds used</h3>
              <ul className="m-0 pl-5 text-sm">
                {Object.entries(retro.review_rounds_used).map(([edge, n]) => <li key={edge}>{edge}: {n}</li>)}
              </ul>
            </>
          )}
        </>
      )}
      <h3 className="m-0 text-sm font-semibold">Overseer interventions and outcomes</h3>
      {report.interventions.length === 0 ? (
        <Empty>The overseer did not intervene.</Empty>
      ) : (
        <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Interventions">
          {report.interventions.map((iv) => (
            <li key={iv.signal_id} data-testid="report-intervention" className="rounded-md border border-border p-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="waiting">{interventionLabel(iv.type ?? "unknown")}</Badge>
                <span className="text-xs text-muted-foreground">{iv.target || "run"}</span>
                {iv.needs_human && <Badge variant="blocked">needs human</Badge>}
                {iv.worked !== null && (
                  <Badge variant={iv.worked ? "complete" : "failed"}>{iv.worked ? "worked" : "did not work"}</Badge>
                )}
              </div>
              <p className="m-0 mt-1">{iv.rationale}</p>
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}

function CarryForwardSection({ report }: { report: RunReportData }) {
  return (
    <Section title="Carry-forward" id="carry-forward">
      {report.carry_forward ? (
        <pre className="m-0 whitespace-pre-wrap rounded-md bg-muted p-3 text-xs" data-testid="carry-forward">{report.carry_forward}</pre>
      ) : (
        <Empty>No carry-forward was written for this run.</Empty>
      )}
    </Section>
  );
}

/** The report body for a loaded payload (also what the unit tests render). */
export function RunReportView({ report }: { report: RunReportData }) {
  return (
    <div className="flex flex-col gap-3">
      <Summary report={report} />
      <CostSection report={report} />
      <BranchesSection report={report} />
      <ScreenshotStrip runId={report.run_id} />
      <WebSection report={report} />
      <CapabilitiesSection report={report} />
      <RetrospectiveSection report={report} />
      <CarryForwardSection report={report} />
    </div>
  );
}

/** Route `/runs/:id/report`: the finished-run report. */
export function RunReport() {
  const { id = "" } = useParams<{ id: string }>();
  const report = useRunReport(id);
  const store = useMemo(() => createRunStore(id), [id]);
  useRunSocket(id, store);
  useEffect(() => {
    getCards(id)
      .then((cards) => store.getState().seedCards(cards))
      .catch(() => undefined);
  }, [id, store]);

  return (
    <div className="page flex flex-col gap-3" data-testid="run-report">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="m-0">Run report</h1>
        <span className="font-mono text-sm">{id}</span>
        <Link to={`/runs/${encodeURIComponent(id)}`} className="text-sm">Back to run</Link>
        {report.data?.meta.project_id && (
          <Link to={`/projects/${encodeURIComponent(report.data.meta.project_id)}`} className="text-sm">
            {report.data.meta.project_id}
          </Link>
        )}
      </div>
      {report.isPending && <p role="status" className="text-sm text-muted-foreground">Loading report…</p>}
      {report.isError && <p role="alert" className="text-sm text-destructive">Could not load the report: {report.error.message}</p>}
      {report.data && (
        <RunStoreProvider store={store}>
          <RunReportView report={report.data} />
        </RunStoreProvider>
      )}
    </div>
  );
}
