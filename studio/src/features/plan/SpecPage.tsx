import { useState } from "react";
import { Alert, Skeleton } from "../../components/ui/form-controls";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/utils";
import { useBacklog, type BacklogItemWire } from "../work/api";
import { itemEvents } from "../work/model";
import { useEditSpec, useSpec, type ChangeWire, type HistoryStamp, type RequirementWire, type SpecWire } from "./api";
import { MethodBadge } from "./DraftPanel";
import { usePmName } from "../pm/api";
import { assumptions, isAssumed, isUnmet, parseValue } from "./SpecView";

type Filter = "all" | "unmet" | "assumed";

/** ● all checks met, ◐ some, ○ none yet. */
export function progressMark(r: RequirementWire): { mark: string; label: string; cls: string } {
  const met = r.checks.filter((c) => c.state === "met").length;
  if (r.checks.length > 0 && met === r.checks.length) return { mark: "●", label: "all checks met", cls: "text-status-complete" };
  if (met > 0) return { mark: "◐", label: "some checks met", cls: "text-status-waiting" };
  return { mark: "○", label: "no check met yet", cls: "text-muted-foreground" };
}

export interface OriginEvent {
  where: string;
  at: number | null;
  /** What this step did ("created", "changed"). */
  what: string;
  summary: string | null;
  because: string | null;
  quote: string | null;
}

/**
 * Where a requirement came from: its origin, then each draft that changed it, oldest first.
 * A history row is the version before a change, stamped with the draft that made the change.
 */
export function originEvents(r: RequirementWire): OriginEvent[] {
  const ev = (h: HistoryStamp, what: string): OriginEvent => ({
    where: h.draft ?? "",
    at: h.at ?? null,
    what,
    summary: h.why?.summary ?? null,
    because: h.why?.because ?? null,
    quote: h.why?.quote ?? null,
  });
  const out: OriginEvent[] = [];
  if (r.origin) out.push(ev(r.origin, "created"));
  // One draft can change several fields; show it once.
  const seen = new Set<string>();
  for (const h of r.history ?? []) {
    const key = h.draft ?? `${h.at}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(ev(h, "changed"));
  }
  return out;
}

/** "Every run starts with a dash." with the params listed after it. */
function Statement({ r }: { r: RequirementWire }) {
  const params = r.params ?? [];
  return (
    <div className="grid gap-1">
      <p className="m-0 text-base leading-relaxed">{r.text}</p>
      {params.length > 0 && (
        <p className="m-0 flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {params.map((p) => (
            <span key={p.key}>
              <span className="font-mono text-muted-foreground">{p.key}</span> ={" "}
              <strong className={cn(p.assumed && "text-status-waiting")}>
                {String(p.value)}
                {p.unit ? ` ${p.unit}` : ""}
              </strong>
              {p.assumed && <span className="ml-1 text-xs text-status-waiting">assumed</span>}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

function AssumedValues({ r, onEdit }: { r: RequirementWire; onEdit?: (c: ChangeWire) => void }) {
  const pmName = usePmName();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const list = assumptions({ version: 0, requirements: [r], decisions: [], rules: [] });
  if (!list.length) return null;
  return (
    <section aria-label="Assumed values" className="grid gap-2 rounded-md border border-status-waiting/40 p-3">
      <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-status-waiting">Assumed by {pmName}</h4>
      {list.map((a) => (
        <div key={a.key ?? "req"} className="flex flex-wrap items-center gap-2 text-sm" data-testid={`assumed-${a.key ?? "req"}`}>
          <span className="min-w-0 flex-1">{a.key ? <><span className="font-mono">{a.key}</span> = {a.value}</> : "the whole requirement"}</span>
          {onEdit && editing !== a.key && (
            <>
              <Button size="sm" variant="outline" className="h-7"
                onClick={() => onEdit(a.key === null || a.raw === null ? { op: "edit_requirement", id: r.id, assumed: false }
                  : { op: "set_param", id: r.id, key: a.key, value: a.raw })}>
                Confirm
              </Button>
              {a.key && <Button size="sm" variant="ghost" className="h-7" onClick={() => { setEditing(a.key); setDraft(String(a.raw)); }}>Edit</Button>}
            </>
          )}
          {onEdit && a.key && editing === a.key && (
            <form className="flex gap-1" onSubmit={(e) => {
              e.preventDefault();
              onEdit({ op: "set_param", id: r.id, key: a.key ?? "", value: parseValue(draft) });
              setEditing(null);
            }}>
              <input aria-label={`New value for ${a.key}`} className="h-7 w-24 rounded border border-border bg-background px-2 text-sm" value={draft} onChange={(e) => setDraft(e.target.value)} />
              <Button size="sm" className="h-7" type="submit">Save</Button>
            </form>
          )}
        </div>
      ))}
    </section>
  );
}

const STATE = {
  met: { mark: "✓", cls: "text-status-complete" },
  unmet: { mark: "✕", cls: "text-status-failed" },
  unknown: { mark: "○", cls: "text-muted-foreground" },
} as const;

function RequirementDetail({ r, onEdit }: { r: RequirementWire; onEdit?: (c: ChangeWire) => void }) {
  const events = originEvents(r);
  return (
    <article className="grid content-start gap-4" data-testid="req-detail" aria-label={`${r.id} ${r.title || r.text}`}>
      <header className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded border border-primary/40 px-1.5 font-mono text-sm text-primary">{r.id}</span>
          <h3 className="m-0 text-xl font-semibold">{r.title || r.text}</h3>
          <span className="text-xs text-muted-foreground">{r.kind} · {r.priority} · v{r.version}</span>
          {r.experiment && <Badge variant="waiting">experiment</Badge>}
        </div>
        <Statement r={r} />
        {r.rationale && <p className="m-0 text-sm text-muted-foreground">{r.rationale}</p>}
      </header>

      <AssumedValues r={r} onEdit={onEdit} />

      <section aria-label="How we know it works" className="grid gap-2">
        <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">How we know it works</h4>
        {r.checks.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">No checks (a non-goal).</p>
        ) : (
          <ul className="m-0 grid list-none gap-2 rounded-md border border-border p-3">
            {r.checks.map((c) => {
              const s = STATE[c.state] ?? STATE.unknown;
              return (
                <li key={c.id} className="grid grid-cols-[1rem_3.5rem_1fr] items-baseline gap-2 text-sm">
                  <span className={s.cls} aria-label={c.state}>{s.mark}</span>
                  <span className="font-mono text-xs text-muted-foreground">{c.id}</span>
                  <span className="min-w-0">
                    {c.text} <MethodBadge method={c.method} />
                    {c.test && <code className="block font-mono text-[11px] text-muted-foreground">test: {c.test}</code>}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-label="Where it came from" className="grid gap-2">
        <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Where it came from</h4>
        {events.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">No recorded reason (written before reasons were kept).</p>
        ) : (
          <ol className="m-0 grid list-none gap-2 p-0" data-testid="origin">
            {events.map((e, i) => (
              <li key={i} className="rounded-r-md border-l-[3px] border-l-primary bg-primary/10 px-3 py-2 text-sm">
                <span className="mr-2 font-mono text-xs text-muted-foreground">{e.where}</span>
                <span className="font-medium">{e.what}</span>
                {e.summary && <span>: {e.summary}</span>}
                {e.quote ? (
                  <p className="m-0 mt-1 text-xs text-muted-foreground">from <q className="italic text-foreground">{e.quote}</q></p>
                ) : e.because ? (
                  <p className="m-0 mt-1 text-xs text-muted-foreground">because {e.because}</p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  );
}

function ServingWork({ r, items }: { r: RequirementWire; items: BacklogItemWire[] }) {
  const work = items.filter((i) => (i.serves ?? []).includes(r.id));
  return (
    <aside aria-label={`Work that serves ${r.id}`} className="grid content-start gap-2" data-testid={`work-${r.id}`}>
      <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Work that serves {r.id}</h4>
      {work.length === 0 && <p className="m-0 text-sm text-muted-foreground">No work item serves it yet.</p>}
      {work.map((i) => {
        const runs = itemEvents(i.history).filter((e) => e.kind === "run");
        return (
          <div key={i.id} className="grid gap-1 rounded-md border border-border p-3 text-sm">
            <p className="m-0">
              <span className="mr-2 font-mono text-xs text-muted-foreground">{i.id}</span>
              <span className="font-medium">{i.title}</span>
            </p>
            <p className="m-0 text-xs text-muted-foreground">
              {i.status}
              {runs.length > 0 && ` · sessions ${[...new Set(runs.map((e) => e.where))].reverse().join(", ")}`}
            </p>
          </div>
        );
      })}
    </aside>
  );
}

/**
 * The spec as one page per requirement (plan §12): a list with progress dots and filters; the
 * requirement's values, checks and where it came from (each draft and the words behind it);
 * and the work that serves it. Decisions and rules follow the list.
 */
export function SpecPageView({ spec, items = [], onEdit }: { spec: SpecWire; items?: BacklogItemWire[]; onEdit?: (c: ChangeWire) => void }) {
  const [filter, setFilter] = useState<Filter>("all");
  const active = spec.requirements.filter((r) => r.status === "active");
  const shown = active.filter((r) => (filter === "unmet" ? isUnmet(r) : filter === "assumed" ? isAssumed(r) : true));
  const [picked, setPicked] = useState<string | null>(null);
  const current = shown.find((r) => r.id === picked) ?? shown[0];
  const decisions = spec.decisions.filter((d) => d.status === "accepted");
  const rules = spec.rules.filter((r) => r.status === "active");
  const count = (f: Filter) => active.filter((r) => (f === "unmet" ? isUnmet(r) : f === "assumed" ? isAssumed(r) : true)).length;
  return (
    <div className="grid gap-4 min-[1100px]:grid-cols-[17rem_minmax(0,1fr)_18rem]">
      <nav aria-label="Requirements" className="grid content-start gap-1">
        <div role="group" aria-label="Filter requirements" className="mb-2 flex flex-wrap gap-1">
          {(["all", "unmet", "assumed"] as const).map((f) => (
            <Button key={f} size="sm" className="h-7 px-2 text-xs" variant={filter === f ? "default" : "outline"} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f === "unmet" ? "Not met" : "Assumed"} {count(f)}
            </Button>
          ))}
        </div>
        {shown.length === 0 && <p className="m-0 text-sm text-muted-foreground">None{filter !== "all" ? " match" : " yet"}.</p>}
        {shown.map((r) => {
          const p = progressMark(r);
          return (
            <button key={r.id} type="button" data-testid={`req-${r.id}`} aria-current={current?.id === r.id || undefined}
              onClick={() => setPicked(r.id)}
              className={cn("flex items-center gap-2 rounded-md border-0 px-2 py-1.5 text-left text-sm",
                current?.id === r.id ? "bg-accent text-foreground" : "bg-transparent text-muted-foreground hover:bg-accent/50")}>
              <span className={p.cls} aria-label={p.label}>{p.mark}</span>
              <span className="font-mono text-xs">{r.id}</span>
              <span className="min-w-0 flex-1 truncate">{r.title || r.text}</span>
              {isAssumed(r) && <span className="text-[11px] text-status-waiting">assumed</span>}
            </button>
          );
        })}
        <p className="m-0 mt-2 text-[11px] text-muted-foreground">● all checks met · ◐ some · ○ none yet</p>
        {decisions.length > 0 && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted-foreground">Decisions ({decisions.length})</summary>
            <ul className="m-0 mt-1 grid list-none gap-2 p-0">
              {decisions.map((d) => (
                <li key={d.id}>
                  <span className="mr-1 font-mono text-xs text-muted-foreground">{d.id}</span>
                  {d.title}: {d.choice}
                  <span className="block text-xs text-muted-foreground">{d.reason}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {rules.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted-foreground">Rules ({rules.length})</summary>
            <ul className="m-0 mt-1 grid list-none gap-2 p-0">
              {rules.map((r) => <li key={r.id}>{r.text} <span className="text-xs text-muted-foreground">({r.strength})</span></li>)}
            </ul>
          </details>
        )}
      </nav>
      {current ? (
        <>
          <RequirementDetail key={current.id} r={current} onEdit={onEdit} />
          <ServingWork r={current} items={items} />
        </>
      ) : (
        <p className="m-0 text-sm text-muted-foreground">No requirements yet. They come from the plan conversation.</p>
      )}
    </div>
  );
}

/** The project's Spec tab. */
export function SpecPage({ projectId }: { projectId: string }) {
  const spec = useSpec(projectId);
  const backlog = useBacklog(projectId);
  const edit = useEditSpec(projectId);
  if (spec.error) return <Alert variant="error">{spec.error.message}</Alert>;
  if (!spec.data) return <Skeleton className="h-40 w-full" />;
  return (
    <div className="grid gap-3">
      {edit.error && <Alert variant="error">{edit.error.message}</Alert>}
      <SpecPageView spec={spec.data} items={backlog.data} onEdit={(c) => edit.mutate(c)} />
    </div>
  );
}
