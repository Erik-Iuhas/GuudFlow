import { useState } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/form-controls";
import { cn } from "../../lib/utils";
import type { ChangeWire, CheckState, ParamValue, RequirementWire, SpecWire } from "./api";
import type { BacklogItemWire } from "../work/api";
import { usePmName } from "../pm/api";
import { MethodBadge } from "./DraftPanel";

const stateMark: Record<CheckState, { mark: string; label: string; cls: string }> = {
  met: { mark: "✓", label: "met", cls: "text-status-complete" },
  unknown: { mark: "○", label: "unknown", cls: "text-muted-foreground" },
  unmet: { mark: "✕", label: "unmet", cls: "text-status-failed" },
};

type Filter = "all" | "unmet" | "assumed";

function isAssumed(r: RequirementWire): boolean {
  return r.assumed || (r.params ?? []).some((p) => p.assumed);
}

function isUnmet(r: RequirementWire): boolean {
  return r.checks.some((c) => c.state !== "met");
}

/** "2" → 2, "true" → true; anything else stays text. */
export function parseValue(text: string): ParamValue {
  const t = text.trim();
  if (t === "true" || t === "false") return t === "true";
  return t !== "" && !Number.isNaN(Number(t)) ? Number(t) : t;
}

export interface Assumption {
  reqId: string;
  title: string;
  /** A param key, or null for the requirement's own "assumed" flag. */
  key: string | null;
  /** What is shown: "10 s", or the requirement's text for the flag. */
  value: string;
  /** The param's own value (null for the flag). */
  raw: ParamValue | null;
}

export function assumptions(spec: SpecWire): Assumption[] {
  const out: Assumption[] = [];
  for (const r of spec.requirements.filter((x) => x.status === "active")) {
    const title = r.title || r.text;
    if (r.assumed) out.push({ reqId: r.id, title, key: null, value: r.text, raw: null });
    for (const p of r.params ?? [])
      if (p.assumed)
        out.push({ reqId: r.id, title, key: p.key, value: `${String(p.value)}${p.unit ? ` ${p.unit}` : ""}`, raw: p.value });
  }
  return out;
}

function AssumptionRow({ a, onEdit }: { a: Assumption; onEdit?: (change: ChangeWire) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(a.raw === null ? "" : String(a.raw));
  // Confirming sets the same value as a person: the server marks it confirmed and pins it.
  const confirm = (): ChangeWire =>
    a.key === null || a.raw === null
      ? { op: "edit_requirement", id: a.reqId, assumed: false }
      : { op: "set_param", id: a.reqId, key: a.key, value: a.raw };
  return (
    <li className="flex flex-wrap items-center gap-2 text-xs" data-testid={`assumption-${a.reqId}-${a.key ?? "req"}`}>
      <span className="font-mono text-muted-foreground">{a.reqId}</span>
      <span className="min-w-0 flex-1 truncate">
        {a.key ? (
          <>
            <span className="font-mono">{a.key}</span> = {a.value}
          </>
        ) : (
          <>whole requirement: {a.title}</>
        )}
      </span>
      {onEdit && !editing && (
        <>
          <Button size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={() => onEdit(confirm())}>
            Confirm
          </Button>
          {a.key && (
            <Button size="sm" variant="ghost" className="h-6 px-2 text-xs" onClick={() => setEditing(true)}>
              Edit
            </Button>
          )}
        </>
      )}
      {onEdit && editing && a.key && (
        <form
          className="flex gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            onEdit({ op: "set_param", id: a.reqId, key: a.key ?? "", value: parseValue(draft) });
            setEditing(false);
          }}
        >
          <Input aria-label={`New value for ${a.key}`} className="h-6 w-24 text-xs" value={draft} onChange={(e) => setDraft(e.target.value)} />
          <Button size="sm" className="h-6 px-2 text-xs" type="submit">
            Save
          </Button>
        </form>
      )}
    </li>
  );
}

function RequirementNode({ r, items }: { r: RequirementWire; items: BacklogItemWire[] }) {
  const work = items.filter((i) => (i.serves ?? []).includes(r.id));
  return (
    <li className="rounded-md border border-border p-3" data-testid={`req-${r.id}`}>
      <p className="text-sm font-medium">
        <span className="mr-2 font-mono text-xs text-muted-foreground">{r.id}</span>
        {r.title || r.text}
      </p>
      {r.title && <p className="mt-1 text-sm">{r.text}</p>}
      <p className="mt-1 flex flex-wrap gap-1 text-xs text-muted-foreground">
        <span>
          {r.kind} · {r.priority}
        </span>
        {r.assumed && <Badge variant="waiting">assumed</Badge>}
        {r.experiment && <Badge variant="waiting">experiment</Badge>}
      </p>
      {(r.params ?? []).length > 0 && (
        <table className="mt-2 text-xs">
          <tbody>
            {(r.params ?? []).map((p) => (
              <tr key={p.key} className={cn(p.assumed && "bg-status-waiting/10")}>
                <th scope="row" className="pr-3 text-left font-mono font-normal text-muted-foreground">
                  {p.key}
                </th>
                <td className="pr-2">
                  {String(p.value)}
                  {p.unit ? ` ${p.unit}` : ""}
                </td>
                <td>{p.assumed && <Badge variant="waiting">assumed</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {r.checks.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 text-xs" aria-label={`Checks of ${r.id}`}>
          {r.checks.map((c) => {
            const s = stateMark[c.state] ?? stateMark.unknown;
            return (
              <li key={c.id} className="flex flex-wrap items-baseline gap-2">
                <span className={s.cls} role="img" aria-label={s.label}>
                  {s.mark}
                </span>
                <span className="font-mono text-muted-foreground">{c.id}</span>
                <MethodBadge method={c.method} />
                <span className="min-w-0 flex-1">
                  {c.text}
                  {c.test && <code className="block font-mono text-[10px] text-muted-foreground">test: {c.test}</code>}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {work.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground" data-testid={`work-${r.id}`}>
          Work: {work.map((i) => `${i.id} ${i.title} (${i.status})`).join("; ")}
        </p>
      )}
    </li>
  );
}

/**
 * The approved spec as a traceability tree (plan §11.3): requirement → params and checks (method,
 * linked test, state) → the work items that serve it; filterable to unmet or assumed; with every
 * assumed value in one checklist to confirm or edit (a person's edit is pinned).
 */
export function SpecView({
  spec,
  items = [],
  onEdit,
}: {
  spec: SpecWire;
  items?: BacklogItemWire[];
  onEdit?: (change: ChangeWire) => void;
}) {
  const pmName = usePmName();
  const [filter, setFilter] = useState<Filter>("all");
  const active = spec.requirements.filter((r) => r.status === "active");
  const reqs = active.filter((r) => (filter === "unmet" ? isUnmet(r) : filter === "assumed" ? isAssumed(r) : true));
  const decisions = spec.decisions.filter((d) => d.status === "accepted");
  const rules = spec.rules.filter((r) => r.status === "active");
  const todo = assumptions(spec);
  return (
    <div className="flex flex-col gap-5">
      <header>
        <h2 className="text-base font-semibold">Spec</h2>
        <p className="text-xs text-muted-foreground">Version {spec.version}</p>
      </header>

      {todo.length > 0 && (
        <section aria-label="Assumptions to confirm" className="flex flex-col gap-2 rounded-md border border-status-waiting/40 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Assumptions to confirm ({todo.length})
          </h3>
          <ul className="flex flex-col gap-1">
            {todo.map((a) => (
              <AssumptionRow key={`${a.reqId}-${a.key ?? "req"}`} a={a} onEdit={onEdit} />
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Requirements" className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Requirements ({reqs.length}
            {filter !== "all" ? ` of ${active.length}` : ""})
          </h3>
          <div role="group" aria-label="Filter requirements" className="inline-flex gap-1">
            {(["all", "unmet", "assumed"] as const).map((f) => (
              <Button key={f} size="sm" className="h-6 px-2 text-xs" variant={filter === f ? "default" : "outline"} aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {f === "all" ? "All" : f === "unmet" ? "Not met yet" : "Assumed"}
              </Button>
            ))}
          </div>
        </div>
        {reqs.length === 0 && <p className="text-sm text-muted-foreground">None{filter !== "all" ? " match" : " yet"}.</p>}
        <ul className="flex flex-col gap-2">
          {reqs.map((r) => (
            <RequirementNode key={r.id} r={r} items={items} />
          ))}
        </ul>
      </section>

      <section aria-label="Decisions" className="flex flex-col gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Decisions ({decisions.length})
        </h3>
        {decisions.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
        <ul className="flex flex-col gap-2">
          {decisions.map((d) => (
            <li key={d.id} className="rounded-md border border-border p-3">
              <p className="text-sm">
                <span className="mr-2 font-mono text-xs text-muted-foreground">{d.id}</span>
                {d.title}: {d.choice}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">{d.reason}</p>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                {d.binding && <Badge variant="outline">binding</Badge>}
                <span>decided by {d.decided_by === "pm" ? pmName : d.decided_by}</span>
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Rules" className="flex flex-col gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Rules ({rules.length})</h3>
        {rules.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
        <ul className="flex flex-col gap-2">
          {rules.map((r) => (
            <li key={r.id} className="rounded-md border border-border p-3 text-sm">
              {r.text}
              <p className="mt-1 text-xs text-muted-foreground">
                {r.strength} · from {r.source}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
