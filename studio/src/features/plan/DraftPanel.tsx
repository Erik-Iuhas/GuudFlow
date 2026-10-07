import { useMemo, useState, type KeyboardEvent } from "react";
import { Alert } from "../../components/ui/form-controls";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/utils";
import type { CheckMethod, DraftWire, SpecWire } from "./api";
import { usePmName } from "../pm/api";
import {
  approveBlocker,
  draftCounts,
  groupChanges,
  refLabel,
  themeGroups,
  type ChangeView,
  type CheckRow,
  type DiffPiece,
  type DraftItem,
  type TeamMember,
} from "./model";

export interface DraftPanelProps {
  draft: DraftWire;
  /** The approved spec, used to show the old text of edits. */
  spec: SpecWire | undefined;
  /** The project's current team, used to show the old values of role edits. */
  team?: TeamMember[];
  /** Backlog items, used to show what an item edit changes. */
  items?: DraftItem[];
  busy?: boolean;
  error?: string | null;
  onTick: (cid: string, accepted: boolean | null) => void;
  onApprove: () => void;
  onDiscard: () => void;
}

const markerClass = { "+": "text-status-complete", "~": "text-status-waiting", "−": "text-status-failed" } as const;

const METHOD_LABEL: Record<CheckMethod, string> = { unit: "test", manual: "manual", visual: "visual", perf: "perf" };

export function MethodBadge({ method }: { method: CheckMethod | null | undefined }) {
  if (!method) return null;
  return (
    <span className="rounded border border-border px-1 text-[10px] uppercase tracking-wide text-muted-foreground">
      {METHOD_LABEL[method]}
    </span>
  );
}

export function Diff({ pieces }: { pieces: DiffPiece[] }) {
  if (pieces.length === 2 && pieces[0].kind === "del" && pieces[1].kind === "add" && pieces[0].text.length > 60) {
    // Mostly rewritten: old and new as two blocks read better than a word diff.
    return (
      <span className="grid gap-1">
        <del className="bg-status-failed/10 text-muted-foreground">{pieces[0].text}</del>
        <ins className="bg-status-complete/15 no-underline">{pieces[1].text}</ins>
      </span>
    );
  }
  return (
    <>
      {pieces.map((p, i) =>
        p.kind === "same" ? (
          <span key={i}>{p.text}</span>
        ) : p.kind === "add" ? (
          <ins key={i} className="bg-status-complete/15 no-underline">{p.text}</ins>
        ) : (
          <del key={i} className="bg-status-failed/10 text-muted-foreground">{p.text}</del>
        ),
      )}
    </>
  );
}

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

function CheckList({ rows }: { rows: CheckRow[] }) {
  return (
    <ul className="mt-1 flex flex-col gap-1 text-xs">
      {rows.map((k) => (
        <li key={k.id} className="grid grid-cols-[auto_1fr] gap-x-1.5" data-testid="check-row">
          <span className="flex items-baseline gap-1.5">
            <span className="font-mono text-muted-foreground">{k.id}</span>
            <MethodBadge method={k.method} />
          </span>
          <span className="min-w-0">
            <Diff pieces={k.text} />
            {k.test && <code className="block font-mono text-[10px] text-muted-foreground">test: {k.test}</code>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Details({ v }: { v: ChangeView }) {
  const pmName = usePmName();
  const c = v.change;
  if (v.section === "Team") return <TeamDetails v={v} />;
  const bits: string[] = [];
  if (c.kind) bits.push(c.kind);
  if (c.priority !== undefined && c.priority !== null) bits.push(String(c.priority));
  const serves = c.serves?.length ? `serves ${c.serves.join(", ")}` : "";
  const after = c.depends_on?.length ? `after ${c.depends_on.map(refLabel).join(", ")}` : "";
  const itemBits = v.section === "Work items" ? [serves, c.size ? `size ${c.size}` : "", after].filter(Boolean) : [];
  return (
    <>
      {c.op === "add_requirement" && c.title && c.text && <p className="mt-1 text-sm">{c.text}</p>}
      {v.textDiff && (
        <p className="mt-1 text-sm" data-testid="text-diff">
          <Diff pieces={v.textDiff} />
        </p>
      )}
      {v.section === "Requirements" && bits.length > 0 && c.op !== "set_param" && (
        <p className="mt-1 text-xs text-muted-foreground">{bits.join(" · ")}</p>
      )}
      {(c.assumed || c.experiment) && c.op !== "set_param" && (
        <p className="mt-1 flex gap-1">
          {c.assumed && <Badge variant="waiting">assumed</Badge>}
          {c.experiment && <Badge variant="waiting">experiment</Badge>}
        </p>
      )}
      {v.params.length > 0 && (
        <table className="mt-1 text-xs" data-testid="param-table">
          <tbody>
            {v.params.map((p) => (
              <tr key={p.key} className={cn(p.assumed && "bg-status-waiting/10")}>
                <th scope="row" className="pr-3 text-left font-mono font-normal text-muted-foreground">
                  {p.key}
                </th>
                <td className="pr-2">
                  {p.old !== null && <del className="mr-1 text-muted-foreground">{p.old}</del>}
                  {p.next !== null ? <span>{p.next}</span> : <span className="text-muted-foreground">removed</span>}
                </td>
                <td>{p.assumed && <Badge variant="waiting">assumed</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {v.checks.length > 0 && <CheckList rows={v.checks} />}
      {v.itemLines.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5 text-xs" data-testid="item-lines" aria-label="Acceptance">
          {v.itemLines.map((l, i) => {
            const body = (
              <>
                {l.ref && <span className="mr-1 font-mono text-muted-foreground">{l.ref}</span>}
                {l.text}
              </>
            );
            return (
              <li key={i} className="flex gap-1.5">
                <span aria-hidden className="w-3 shrink-0 font-mono">{l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}</span>
                {l.kind === "add" ? <ins className="bg-status-complete/15 no-underline">{body}</ins>
                  : l.kind === "del" ? <del className="text-muted-foreground">{body}</del>
                  : <span className="text-muted-foreground">{body}</span>}
              </li>
            );
          })}
        </ul>
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
        <p className="mt-1 text-xs text-muted-foreground">decided by {pmName}</p>
      )}
    </>
  );
}

function ChangeRow({ v, focused, onTick }: { v: ChangeView; focused: boolean; onTick: DraftPanelProps["onTick"] }) {
  // Field ops edit part of a requirement: name the requirement, not just the op.
  const label = v.reqId && v.reqTitle && v.change.op !== "add_requirement" ? `${v.label} · ${v.reqTitle}` : v.label;
  return (
    <li
      className={cn("flex gap-3 rounded-md border border-border p-3", focused && "ring-2 ring-primary")}
      data-testid={`change-${v.cid}`}
      aria-current={focused || undefined}
    >
      <span aria-hidden="true" className={cn("w-4 shrink-0 text-center font-mono font-semibold", markerClass[v.marker])}>
        {v.marker}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm">
          <span className="mr-2 font-mono text-xs text-muted-foreground">{label}</span>
          {v.oldText !== null && !v.textDiff && <del className="mr-2 text-muted-foreground">{v.oldText}</del>}
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

function SectionList({ views, focusedCid, onTick }: { views: ChangeView[]; focusedCid: string | null; onTick: DraftPanelProps["onTick"] }) {
  const bySection = new Map<string, ChangeView[]>();
  for (const v of views) bySection.set(v.section, [...(bySection.get(v.section) ?? []), v]);
  return (
    <>
      {[...bySection.entries()].map(([section, list]) => (
        <section key={section} aria-label={section} className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {section} ({list.length})
          </h4>
          <ul className="flex flex-col gap-2">
            {list.map((v) => (
              <ChangeRow key={v.cid} v={v} focused={v.cid === focusedCid} onTick={onTick} />
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}

/**
 * The open draft (plan §11.3): changes by theme when the project manager grouped them, each
 * theme tickable as a whole, then by section; structured diffs for statements, params and
 * checks. Keyboard: j/k move, y/n accept or reject the focused change.
 */
export function DraftPanel({ draft, spec, team, items, busy, error, onTick, onApprove, onDiscard }: DraftPanelProps) {
  const counts = draftCounts(draft);
  const blocker = approveBlocker(counts);
  const sectionGroups = groupChanges(draft, spec, team, items);
  const views = useMemo(() => sectionGroups.flatMap((g) => g.changes), [sectionGroups]);
  const themes = themeGroups(views);
  const ordered = themes.flatMap((t) => t.changes);
  const [focus, setFocus] = useState(0);
  const focusedCid = ordered.length ? ordered[Math.min(focus, ordered.length - 1)].cid : null;

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLInputElement || !ordered.length) return;
    const at = Math.min(focus, ordered.length - 1);
    if (e.key === "j") setFocus(Math.min(at + 1, ordered.length - 1));
    else if (e.key === "k") setFocus(Math.max(at - 1, 0));
    else if (e.key === "y" || e.key === "n") {
      const v = ordered[at];
      const want = e.key === "y";
      onTick(v.cid, v.accepted === want ? null : want);
    } else return;
    e.preventDefault();
  }

  const grouped = themes.some((t) => t.group !== null);
  return (
    <div className="flex flex-col gap-4 outline-none" tabIndex={0} onKeyDown={onKey} aria-label="Draft changes; j and k move, y and n accept or reject">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Spec · draft</h2>
          <p className="text-xs text-muted-foreground">
            {counts.total} change{counts.total === 1 ? "" : "s"} · {counts.accepted} accepted · {counts.undecided}{" "}
            undecided
          </p>
          <p className="text-[11px] text-muted-foreground">
            Keys: <kbd>j</kbd>/<kbd>k</kbd> move, <kbd>y</kbd>/<kbd>n</kbd> accept or reject
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
      {views.length === 0 && <p className="text-sm text-muted-foreground">The draft has no changes yet.</p>}
      {grouped
        ? themes.map((t) => (
            <details key={t.group ?? "(other)"} open className="rounded-md border border-border p-3" data-testid="theme">
              <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                <h3 className="text-sm font-semibold">{t.group ?? "Other changes"}</h3>
                <span className="text-xs text-muted-foreground">
                  {t.changes.length} change{t.changes.length === 1 ? "" : "s"}
                </span>
                <span className="ml-auto flex gap-1">
                  <Button size="sm" variant="outline" className="h-7" disabled={busy}
                    onClick={(e) => { e.preventDefault(); t.changes.forEach((v) => v.accepted !== true && onTick(v.cid, true)); }}>
                    Accept all
                  </Button>
                  <Button size="sm" variant="outline" className="h-7" disabled={busy}
                    onClick={(e) => { e.preventDefault(); t.changes.forEach((v) => v.accepted !== false && onTick(v.cid, false)); }}>
                    Reject all
                  </Button>
                </span>
              </summary>
              <div className="mt-3 flex flex-col gap-3">
                <SectionList views={t.changes} focusedCid={focusedCid} onTick={onTick} />
              </div>
            </details>
          ))
        : <SectionList views={views} focusedCid={focusedCid} onTick={onTick} />}
    </div>
  );
}
