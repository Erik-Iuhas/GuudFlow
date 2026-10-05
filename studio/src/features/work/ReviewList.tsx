import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Alert, Textarea } from "../../components/ui/form-controls";
import { cn } from "../../lib/utils";
import type { RunBranchWire, RunState } from "./api";
import { changeSummary } from "./model";

const STATE_VARIANT: Record<RunState, BadgeProps["variant"]> = {
  pending: "waiting",
  accepted: "complete",
  rejected: "failed",
  no_changes: "outline",
};
const STATE_LABEL: Record<RunState, string> = {
  pending: "waiting for review",
  accepted: "accepted",
  rejected: "rejected",
  no_changes: "no changes",
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
  onDecide: (runId: string, accept: boolean, note: string) => void;
  onContinue: (runId: string) => void;
  busyRunId?: string | null;
  error?: DecisionError | null;
}

/**
 * Runs handed back to the project (plan §13). Nothing reaches the project's main
 * until a human accepts it here; the diff is what accepting would bring in.
 */
export function ReviewList({
  pending, decided, changes, onViewChanges, onDecide, onContinue, busyRunId, error,
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
              diff={changes[run.run_id]}
              requested={run.run_id in changes}
              busy={busyRunId === run.run_id}
              error={error?.runId === run.run_id ? error : null}
              onViewChanges={() => onViewChanges(run.run_id)}
              onDecide={(accept, note) => onDecide(run.run_id, accept, note)}
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
  run, diff, requested, busy, error, onViewChanges, onDecide, onContinue,
}: {
  run: RunBranchWire;
  diff: string | undefined;
  requested: boolean;
  busy: boolean;
  error: DecisionError | null;
  onViewChanges: () => void;
  onDecide: (accept: boolean, note: string) => void;
  onContinue: () => void;
}) {
  const [note, setNote] = useState("");
  return (
    <li className="rounded-lg border border-border bg-card p-3 text-card-foreground" data-testid={`review-${run.run_id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/runs/${encodeURIComponent(run.run_id)}/report`} className="font-mono text-sm font-semibold">
          {run.run_id}
        </Link>
        <Badge variant={STATE_VARIANT[run.state]}>{STATE_LABEL[run.state]}</Badge>
        <span className="text-sm text-muted-foreground">{changeSummary(run)}</span>
        {run.parent_run && <span className="text-xs text-muted-foreground">continues {run.parent_run}</span>}
      </div>
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
      <div className="mt-2 grid gap-2">
        <Textarea
          aria-label={`Note for ${run.run_id}`}
          placeholder="Optional note (why you accept or reject it)"
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => onDecide(true, note)} disabled={busy}>
            Accept into the project
          </Button>
          <Button size="sm" variant="outline" onClick={() => onDecide(false, note)} disabled={busy}>
            Reject
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="error" className="mt-2">
          {error.message}
          {error.files.length > 0 && (
            <ul className="m-0 mt-1 pl-4 font-mono text-xs">
              {error.files.map((f) => <li key={f}>{f}</li>)}
            </ul>
          )}
        </Alert>
      )}
    </li>
  );
}

function DiffBlock({ text }: { text: string }) {
  return (
    <pre className="m-0 mt-2 max-h-80 overflow-auto rounded-md border border-border bg-muted p-2 font-mono text-xs" data-testid="review-diff">
      {text.split("\n").map((line, i) => (
        <div
          key={i}
          className={cn(
            line.startsWith("+") && !line.startsWith("+++") && "text-status-complete",
            line.startsWith("-") && !line.startsWith("---") && "text-status-failed",
          )}
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}
