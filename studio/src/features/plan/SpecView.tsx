import { Badge } from "../../components/ui/badge";
import type { CheckState, SpecWire } from "./api";
import { usePmName } from "../pm/api";

const stateMark: Record<CheckState, { mark: string; label: string; cls: string }> = {
  met: { mark: "✓", label: "met", cls: "text-status-complete" },
  unknown: { mark: "○", label: "unknown", cls: "text-muted-foreground" },
  unmet: { mark: "✕", label: "unmet", cls: "text-status-failed" },
};

/** The approved spec: requirements with check states, decisions and rules. */
export function SpecView({ spec }: { spec: SpecWire }) {
  const pmName = usePmName();
  const reqs = spec.requirements.filter((r) => r.status === "active");
  const decisions = spec.decisions.filter((d) => d.status === "accepted");
  const rules = spec.rules.filter((r) => r.status === "active");
  return (
    <div className="flex flex-col gap-5">
      <header>
        <h2 className="text-base font-semibold">Spec</h2>
        <p className="text-xs text-muted-foreground">Version {spec.version}</p>
      </header>

      <section aria-label="Requirements" className="flex flex-col gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Requirements ({reqs.length})
        </h3>
        {reqs.length === 0 && <p className="text-sm text-muted-foreground">None yet.</p>}
        <ul className="flex flex-col gap-2">
          {reqs.map((r) => (
            <li key={r.id} className="rounded-md border border-border p-3" data-testid={`req-${r.id}`}>
              <p className="text-sm">
                <span className="mr-2 font-mono text-xs text-muted-foreground">{r.id}</span>
                {r.text}
              </p>
              <p className="mt-1 flex flex-wrap gap-1 text-xs text-muted-foreground">
                <span>
                  {r.kind} · {r.priority}
                </span>
                {r.assumed && <Badge variant="waiting">assumed</Badge>}
                {r.experiment && <Badge variant="waiting">experiment</Badge>}
              </p>
              {r.checks.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1 text-xs">
                  {r.checks.map((c) => {
                    const s = stateMark[c.state] ?? stateMark.unknown;
                    return (
                      <li key={c.id} className="flex gap-2">
                        <span className={s.cls} role="img" aria-label={s.label}>
                          {s.mark}
                        </span>
                        <span className="font-mono text-muted-foreground">{c.id}</span>
                        <span>{c.text}</span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
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
