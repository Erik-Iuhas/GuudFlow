import { useState } from "react";
import { AskJack } from "../jack/AskJack";
import { Link } from "react-router-dom";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Alert, Textarea } from "../../components/ui/form-controls";
import { DiffText } from "../report/DiffViewer";
import type { CheckWire, PublishWire, RunBranchWire, RunState } from "./api";
import { changeSummary } from "./model";
import { COSIGN_WHY, Cosign, canAccept } from "./Cosign";
import { SpecResults } from "./SpecResults";
import { Ledger } from "./Ledger";
import { OwnerChecks, StopReason } from "./OwnerChecks";

const STATE_VARIANT: Record<RunState, BadgeProps["variant"]> = {
  pending: "waiting",
  accepted: "complete",
  rejected: "failed",
  no_changes: "outline",
  superseded: "outline",
};
const STATE_LABEL: Record<RunState, string> = {
  pending: "waiting for review",
  accepted: "accepted",
  rejected: "rejected",
  no_changes: "no changes",
  superseded: "continued by a later run",
};

const CHECK_VARIANT: Record<CheckWire["status"], BadgeProps["variant"]> = {
  passed: "complete",
  failed: "failed",
  timed_out: "failed",
  skipped: "outline",
};
const CHECK_LABEL: Record<CheckWire["status"], string> = {
  passed: "check passed",
  failed: "check failed",
  timed_out: "check timed out",
  skipped: "check skipped",
};

export interface DecisionError {
  runId: string;
  message: string;
  files: string[];
}

export interface ReviewListProps {
  pending: RunBranchWire[];
  decided: RunBranchWire[];
  /** Diff text per run whose changes were requested (undefined while loading). */
  changes: Record<string, string | undefined>;
  onViewChanges: (runId: string) => void;
  onDecide: (runId: string, accept: boolean, note: string, confirmed?: string[]) => void;
  onContinue: (runId: string) => void;
  busyRunId?: string | null;
  error?: DecisionError | null;
  /** When given, each waiting run shows how it did against the spec and, if needed, the co-sign area. */
  projectId?: string;
}

/**
 * Runs handed back to the project (plan §13). Nothing reaches the project's main
 * until a human accepts it here; the diff is what accepting would bring in.
 */
export function ReviewList({
  pending, decided, changes, onViewChanges, onDecide, onContinue, busyRunId, error, projectId,
}: ReviewListProps) {
  return (
    <section aria-label="Runs waiting for review" className="flex flex-col gap-3">
      {pending.length === 0 ? (
        <p className="m-0 text-sm text-muted-foreground">
          No run is waiting for review. Finished runs show up here before anything reaches the project.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {pending.map((run) => (
            <PendingRun
              key={run.run_id}
              run={run}
              projectId={projectId}
              diff={changes[run.run_id]}
              requested={run.run_id in changes}
              busy={busyRunId === run.run_id}
              error={error?.runId === run.run_id ? error : null}
              onViewChanges={() => onViewChanges(run.run_id)}
              onDecide={(accept, note, confirmed) => onDecide(run.run_id, accept, note, confirmed)}
              onContinue={() => onContinue(run.run_id)}
            />
          ))}
        </ul>
      )}
      {decided.length > 0 && (
        <details>
          <summary className="cursor-pointer text-sm text-muted-foreground">
            Earlier runs ({decided.length})
          </summary>
          <ul className="m-0 mt-2 flex list-none flex-col gap-1 p-0 text-sm">
            {decided.map((run) => (
              <li key={run.run_id} className="flex flex-wrap items-center gap-2">
                <Link to={`/runs/${encodeURIComponent(run.run_id)}/report`} className="font-mono text-xs">
                  {run.run_id}
                </Link>
                <Badge variant={STATE_VARIANT[run.state]}>{STATE_LABEL[run.state]}</Badge>
                {run.state !== "no_changes" && <span className="text-muted-foreground">{changeSummary(run)}</span>}
                {run.published && <Published result={run.published} />}
                {run.note && <span className="text-muted-foreground">— {run.note}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function PendingRun({
  run, projectId, diff, requested, busy, error, onViewChanges, onDecide, onContinue,
}: {
  run: RunBranchWire;
  projectId?: string;
  diff: string | undefined;
  requested: boolean;
  busy: boolean;
  error: DecisionError | null;
  onViewChanges: () => void;
  onDecide: (accept: boolean, note: string, confirmed?: string[]) => void;
  onContinue: () => void;
}) {
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState<string[]>([]);
  const allowed = canAccept(run);
  return (
    <li className="rounded-lg border border-border bg-card p-3 text-card-foreground" data-testid={`review-${run.run_id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/runs/${encodeURIComponent(run.run_id)}/report`} className="font-mono text-sm font-semibold">
          {run.run_id}
        </Link>
        <Badge variant={STATE_VARIANT[run.state]}>{STATE_LABEL[run.state]}</Badge>
        <span className="text-sm text-muted-foreground">{changeSummary(run)}</span>
        {run.parent_run && <span className="text-xs text-muted-foreground">continues {run.parent_run}</span>}
        {run.check && <Badge variant={CHECK_VARIANT[run.check.status]}>{CHECK_LABEL[run.check.status]}</Badge>}
        {projectId && (
          <span className="ml-auto">
            <AskJack label="What should I check?" place={{ projectId, page: "run", run_id: run.run_id }}
              question="This session is waiting for my review. What should I look at first, which checks are mine, and is it safe to accept?" />
          </span>
        )}
      </div>
      {run.check && run.check.status !== "passed" && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {run.check.reason || `\`${run.check.command}\` exited with ${run.check.exit_code ?? "a timeout"}`}
          </summary>
          <pre className="m-0 mt-1 max-h-60 overflow-auto rounded-md border border-border bg-muted p-2 font-mono text-xs">
            {run.check.output || "(no output)"}
          </pre>
        </details>
      )}
      {run.files.length > 0 && (
        <p className="m-0 mt-1 truncate font-mono text-xs text-muted-foreground" title={run.files.join(", ")}>
          {run.files.slice(0, 8).join(", ")}{run.files.length > 8 ? " …" : ""}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={onViewChanges} disabled={requested && diff === undefined}>
          {requested && diff === undefined ? "Loading…" : requested ? "Refresh changes" : "View changes"}
        </Button>
        <Button variant="outline" size="sm" onClick={onContinue}>Continue this run</Button>
      </div>
      {diff !== undefined && <DiffBlock text={diff} />}
      {run.check?.ledger && Object.keys(run.check.ledger).length > 0 && (
        <Ledger entries={Object.values(run.check.ledger)} confirmed={confirmed}
          onToggle={(id) => setConfirmed(confirmed.includes(id) ? confirmed.filter((x) => x !== id) : [...confirmed, id])} />
      )}
      {run.stopped && <StopReason stopped={run.stopped} />}
      {run.state === "pending" && (run.owner_checks?.length ?? 0) > 0 && (
        <OwnerChecks checks={run.owner_checks ?? []} confirmed={confirmed}
          onToggle={(id) => setConfirmed(confirmed.includes(id) ? confirmed.filter((x) => x !== id) : [...confirmed, id])} />
      )}
      {projectId && <SpecResults runId={run.run_id} />}
      {projectId && run.needs_cosign && <Cosign projectId={projectId} run={run} onViewChanges={onViewChanges} />}
      <div className="mt-2 grid gap-2">
        <Textarea
          aria-label={`Note for ${run.run_id}`}
          placeholder="Optional note (why you accept or reject it)"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <span title={allowed ? undefined : COSIGN_WHY}>
            <Button size="sm" onClick={() => onDecide(true, note, confirmed)} disabled={busy || !allowed}
              aria-describedby={allowed ? undefined : `why-${run.run_id}`}>
              Accept into the project
            </Button>
          </span>
          {!allowed && <span id={`why-${run.run_id}`} className="sr-only">{COSIGN_WHY}</span>}
          <Button size="sm" variant="outline" onClick={() => onDecide(false, note)} disabled={busy}>
            Reject
          </Button>
          {projectId && (
            <Link to="?tab=plan" className="self-center text-sm">Open as plan draft</Link>
          )}
        </div>
      </div>
      {error && (
        <Alert variant="error" className="mt-2">
          {error.message}
          {error.files.length > 0 && (
            <>
              <ul className="m-0 mt-1 pl-4 font-mono text-xs">
                {error.files.map((f) => <li key={f}>{f}</li>)}
              </ul>
              <Button size="sm" variant="outline" className="mt-2" onClick={onContinue}>
                Reconcile in a new run
              </Button>
            </>
          )}
        </Alert>
      )}
    </li>
  );
}

function Published({ result }: { result: PublishWire }) {
  if (result.status === "skipped") return null;
  if (result.status === "error") {
    return <span className="text-xs text-status-failed">publish failed: {result.detail}</span>;
  }
  if (result.url) {
    return <a href={result.url} target="_blank" rel="noreferrer" className="text-xs">pull request</a>;
  }
  return <span className="font-mono text-xs text-muted-foreground">pushed {result.ref}</span>;
}

function DiffBlock({ text }: { text: string }) {
  return (
    <div className="mt-2 max-h-[32rem] overflow-auto rounded-md border border-border" data-testid="review-diff">
      <DiffText text={text} />
    </div>
  );
}
