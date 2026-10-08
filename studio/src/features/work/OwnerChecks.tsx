import type { OwnerCheckWire, RunStoppedWire } from "./api";

/** The spec check id a line names ("R16.3: ..."), if any. */
export function checkIdOf(line: string): string | null {
  const m = line.match(/^\s*(R\d+\.\d+)\s*:/i);
  return m ? m[1].toUpperCase() : null;
}

/** Checks only the owner can do. Ticking one that names a spec check records it as confirmed by you. */
export function OwnerChecks({ checks, confirmed, onToggle }: {
  checks: OwnerCheckWire[];
  confirmed: string[];
  onToggle: (checkId: string) => void;
}) {
  if (checks.length === 0) return null;
  return (
    <section aria-label="Your checks" className="mt-2 rounded-md border border-border p-2" data-testid="owner-checks">
      <h4 className="m-0 text-sm font-semibold">Your checks before accepting</h4>
      <p className="m-0 mt-0.5 text-xs text-muted-foreground">
        The workers could not do these themselves. Try each one; tick what you confirmed.
      </p>
      <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0">
        {checks.map((c) => {
          const id = checkIdOf(c.item);
          return (
            <li key={`${c.task_id}-${c.item}`} className="text-sm">
              <label className="flex gap-2">
                {id ? (
                  <input type="checkbox" className="mt-1" checked={confirmed.includes(id)}
                    onChange={() => onToggle(id)} aria-label={`Confirmed ${id}`} />
                ) : (
                  <span aria-hidden="true" className="mt-0.5 w-3.5 shrink-0">•</span>
                )}
                <span className="min-w-0">
                  {c.item}
                  <span className="ml-1 font-mono text-xs text-muted-foreground">({c.task_id})</span>
                  {c.how && <span className="block text-xs text-muted-foreground">Try: {c.how}</span>}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Why a run ended before its cards were done, and what each open card waits on. */
export function StopReason({ stopped }: { stopped: RunStoppedWire }) {
  return (
    <details className="mt-2 rounded-md border border-border p-2" data-testid="stop-reason" open>
      <summary className="cursor-pointer text-sm font-medium">{stopped.summary}</summary>
      <ul className="m-0 mt-1 pl-4 text-xs">
        {stopped.open_cards.map((c) => (
          <li key={c.task_id}>
            <span className="font-mono">{c.task_id}</span> {c.title} ({c.owner}): {c.waits_on}
          </li>
        ))}
      </ul>
      <p className="m-0 mt-1 text-xs text-muted-foreground">
        “Continue this run” starts a run with only the unfinished items; the finished ones close when you accept it.
      </p>
    </details>
  );
}
