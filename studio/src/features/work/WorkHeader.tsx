import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { formatUsd } from "../../lib/cost";
import type { AutopilotStatus, Proposal, RemoteSummary, SyncWire } from "./api";

const SYNC_TEXT: Record<SyncWire["status"], string> = {
  seeded: "started from the remote",
  up_to_date: "up to date",
  fast_forward: "brought in the remote's new commits",
  merged: "merged the remote's changes",
  conflict: "not synced: conflict",
  error: "sync failed",
};

function when(ts: number | null | undefined): string {
  if (!ts) return "now";
  return new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Where the project's code comes from and goes to, and the autopilot (plan §14.1, §14.7). */
export function WorkHeader({
  remote, autopilot, lastSync, onSync, syncing,
}: {
  remote: RemoteSummary | null | undefined;
  autopilot: AutopilotStatus | undefined;
  lastSync: SyncWire | null;
  onSync: () => void;
  syncing?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-card px-3 py-2 text-sm"
      data-testid="work-header">
      {remote ? (
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">Remote</span>
          <span className="font-mono text-xs">{remote.url}</span>
          <Badge variant="outline">{remote.branch}</Badge>
          {remote.publish !== "none" && <Badge variant="outline">publish: {remote.publish}</Badge>}
          {!remote.token && remote.publish === "pr" && (
            <span className="text-xs text-status-blocked">no token set</span>
          )}
          <Button size="sm" variant="outline" onClick={onSync} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync now"}
          </Button>
          {lastSync && (
            <span className={lastSync.status === "conflict" || lastSync.status === "error"
              ? "text-xs text-status-failed" : "text-xs text-muted-foreground"}>
              {SYNC_TEXT[lastSync.status]}{lastSync.detail ? `: ${lastSync.detail}` : ""}
            </span>
          )}
        </span>
      ) : (
        <span className="text-muted-foreground">No remote: accepted work stays in GuudFlow (set one in Settings).</span>
      )}
      {autopilot?.error ? (
        <span className="text-xs text-status-failed">Autopilot: {autopilot.error}</span>
      ) : autopilot?.enabled ? (
        <span className="text-xs text-muted-foreground" data-testid="autopilot-status">
          Autopilot on · next run {when(autopilot.next_due)} ·{" "}
          {formatUsd(autopilot.week_spend_usd ?? 0)} of {formatUsd(autopilot.max_usd_per_week ?? 0)} this week
        </span>
      ) : null}
    </div>
  );
}

/** Items the model suggests from the roadmap; nothing is added until the user says so. */
export function Suggestions({
  proposals, onAdd, onDismiss,
}: {
  proposals: Proposal[];
  onAdd: (p: Proposal) => void;
  onDismiss: () => void;
}) {
  return (
    <section aria-label="Suggested items" className="rounded-lg border border-dashed border-border p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="m-0 text-sm font-semibold">Suggested from the roadmap</h3>
        <Button size="sm" variant="ghost" onClick={onDismiss}>Dismiss</Button>
      </div>
      {proposals.length === 0 ? (
        <p className="m-0 text-sm text-muted-foreground">No new items to suggest.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {proposals.map((p) => (
            <li key={p.title} className="flex items-start justify-between gap-2" data-testid="suggestion">
              <div className="min-w-0">
                <div className="font-medium">{p.title} <Badge variant="outline">{p.kind}</Badge></div>
                {p.acceptance.length > 0 && (
                  <ul className="m-0 pl-4 text-sm text-muted-foreground">
                    {p.acceptance.map((a) => <li key={a}>{a}</li>)}
                  </ul>
                )}
                {p.why && <p className="m-0 text-xs text-muted-foreground">{p.why}</p>}
              </div>
              <Button size="sm" variant="outline" onClick={() => onAdd(p)}>Add</Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
