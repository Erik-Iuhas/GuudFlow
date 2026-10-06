import { Alert } from "../../components/ui/form-controls";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/utils";
import type { DraftWire, SpecWire } from "./api";
import { approveBlocker, draftCounts, groupChanges, refLabel, type ChangeView, type TeamMember } from "./model";

export interface DraftPanelProps {
  draft: DraftWire;
  /** The approved spec, used to show the old text of edits. */
  spec: SpecWire | undefined;
  /** The project's current team, used to show the old values of role edits. */
  team?: TeamMember[];
  busy?: boolean;
  error?: string | null;
  onTick: (cid: string, accepted: boolean | null) => void;
  onApprove: () => void;
  onDiscard: () => void;
}

const markerClass = { "+": "text-status-complete", "~": "text-status-waiting", "−": "text-status-failed" } as const;

function TeamDetails({ v }: { v: ChangeView }) {
  const c = v.change;
  const lines: string[] = [];
  if (c.op === "add_role") {
    if (c.depends_on?.length) lines.push(`after ${c.depends_on.join(", ")}`);
    if (c.review_of?.length) lines.push(`reviews ${c.review_of.join(", ")}`);
  }
  return (
    <>
      {c.op === "add_role" && c.description && <p className="mt-1 text-xs text-muted-foreground">{c.description}</p>}
      {lines.length > 0 && <p className="mt-1 text-xs text-muted-foreground">{lines.join(" · ")}</p>}
      {v.teamFields.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 text-xs text-muted-foreground">
          {v.teamFields.map((f) => (
            <li key={f.field}>
              {f.field}: {f.old !== null && <del className="mr-1">{f.old}</del>}
              <span className="text-foreground">{f.next}</span>
            </li>
          ))}
        </ul>
      )}
      {c.reason && <p className="mt-1 text-xs text-muted-foreground">{c.reason}</p>}
    </>
  );
}

function Details({ v }: { v: ChangeView }) {
  const c = v.change;
  if (v.section === "Team") return <TeamDetails v={v} />;
  const bits: string[] = [];
  if (c.kind) bits.push(c.kind);
  if (c.priority !== undefined && c.priority !== null) bits.push(String(c.priority));
  if (c.size) bits.push(`size ${c.size}`);
  const serves = c.serves?.length ? `serves ${c.serves.join(", ")}` : "";
  const after = c.depends_on?.length ? `after ${c.depends_on.map(refLabel).join(", ")}` : "";
  const itemBits = v.section === "Work items" ? [serves, c.size ? `size ${c.size}` : "", after].filter(Boolean) : [];
  const checks = c.op === "add_requirement" ? (c.checks ?? []) : [];
  return (
    <>
      {v.section === "Requirements" && bits.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">{bits.join(" · ")}</p>
      )}
      {(c.assumed || c.experiment) && (
        <p className="mt-1 flex gap-1">
          {c.assumed && <Badge variant="waiting">assumed</Badge>}
          {c.experiment && <Badge variant="waiting">experiment</Badge>}
        </p>
      )}
      {checks.length > 0 && (
        <ol className="mt-1 list-decimal pl-5 text-xs text-muted-foreground">
          {checks.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ol>
      )}
      {itemBits.length > 0 && <p className="mt-1 text-xs text-muted-foreground">{itemBits.join(" · ")}</p>}
      {v.section === "Decisions" && c.reason && <p className="mt-1 text-xs text-muted-foreground">{c.reason}</p>}
      {v.section === "Decisions" && (c.binding || serves) && (
        <p className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
          {c.binding && <Badge variant="outline">binding</Badge>}
          {serves}
        </p>
      )}
      {v.section === "Decisions" && v.byPm && v.accepted === true && (
        <p className="mt-1 text-xs text-muted-foreground">decided by the project manager</p>
      )}
    </>
  );
}

function ChangeRow({ v, onTick }: { v: ChangeView; onTick: DraftPanelProps["onTick"] }) {
  return (
    <li className="flex gap-3 rounded-md border border-border p-3" data-testid={`change-${v.cid}`}>
      <span aria-hidden="true" className={cn("w-4 shrink-0 text-center font-mono font-semibold", markerClass[v.marker])}>
        {v.marker}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm">
          <span className="mr-2 font-mono text-xs text-muted-foreground">{v.label}</span>
          {v.oldText !== null && <del className="mr-2 text-muted-foreground">{v.oldText}</del>}
          <span className={cn(v.accepted === false && "text-muted-foreground line-through")}>{v.summary}</span>
        </p>
        <Details v={v} />
      </div>
      <div className="flex shrink-0 gap-1">
        <Button
          size="icon"
          variant={v.accepted === true ? "default" : "outline"}
          className="h-8 w-8"
          aria-pressed={v.accepted === true}
          aria-label={`Accept ${v.name}`}
          onClick={() => onTick(v.cid, v.accepted === true ? null : true)}
        >
          ✓
        </Button>
        <Button
          size="icon"
          variant={v.accepted === false ? "destructive" : "outline"}
          className="h-8 w-8"
          aria-pressed={v.accepted === false}
          aria-label={`Reject ${v.name}`}
          onClick={() => onTick(v.cid, v.accepted === false ? null : false)}
        >
          ✕
        </Button>
      </div>
    </li>
  );
}

/** The open draft: its changes by section with ✓/✕ ticks, then Approve or Discard. */
export function DraftPanel({ draft, spec, team, busy, error, onTick, onApprove, onDiscard }: DraftPanelProps) {
  const counts = draftCounts(draft);
  const blocker = approveBlocker(counts);
  const groups = groupChanges(draft, spec, team);
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Spec · draft</h2>
          <p className="text-xs text-muted-foreground">
            {counts.total} change{counts.total === 1 ? "" : "s"} · {counts.accepted} accepted · {counts.undecided}{" "}
            undecided
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" disabled={busy} onClick={onDiscard}>
            Discard draft
          </Button>
          <span title={blocker || undefined}>
            <Button size="sm" disabled={busy || counts.undecided > 0} onClick={onApprove}>
              Approve draft
            </Button>
          </span>
        </div>
      </header>
      {error && <Alert variant="error">{error}</Alert>}
      {groups.length === 0 && <p className="text-sm text-muted-foreground">The draft has no changes yet.</p>}
      {groups.map((g) => (
        <section key={g.section} aria-label={g.section} className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {g.section} ({g.changes.length})
          </h3>
          <ul className="flex flex-col gap-2">
            {g.changes.map((v) => (
              <ChangeRow key={v.cid} v={v} onTick={onTick} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
