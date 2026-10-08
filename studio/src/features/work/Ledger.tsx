import { Badge, type BadgeProps } from "../../components/ui/badge";
import type { LedgerEntryWire, LedgerState } from "./api";

// The verification ledger at review (role contracts plan §4.4): what a test proved, what an
// agent only claimed, and what needs your eyes.

const ORDER: LedgerState[] = ["failing", "missing_test", "unmet", "unchecked", "claimed", "person", "verified"];

export const LEDGER_LABEL: Record<LedgerState, string> = {
  verified: "verified by a test",
  failing: "test failing",
  missing_test: "no test found",
  claimed: "claimed by the team",
  unmet: "reported unmet",
  unchecked: "not checked",
  person: "confirmed by you",
};

const VARIANT: Record<LedgerState, BadgeProps["variant"]> = {
  verified: "complete",
  person: "complete",
  failing: "failed",
  missing_test: "failed",
  unmet: "failed",
  claimed: "waiting",
  unchecked: "outline",
};

/** A person can confirm a check that no test settled (failing or missing tests stay as they are). */
export function confirmable(e: LedgerEntryWire): boolean {
  return e.state === "claimed" || e.state === "unchecked" || e.state === "unmet";
}

export function Ledger({
  entries, confirmed, onToggle,
}: {
  entries: LedgerEntryWire[];
  confirmed: string[];
  onToggle: (checkId: string) => void;
}) {
  const sorted = [...entries].sort((a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state)
    || a.check.localeCompare(b.check, undefined, { numeric: true }));
  const counts = ORDER.map((s) => [s, entries.filter((e) => e.state === s).length] as const)
    .filter(([, n]) => n > 0);
  return (
    <details className="mt-2" open={entries.some((e) => e.state !== "verified")} data-testid="ledger">
      <summary className="cursor-pointer text-sm">
        Checks: {counts.map(([s, n]) => `${n} ${LEDGER_LABEL[s]}`).join(", ")}
      </summary>
      <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
        {sorted.map((e) => (
          <li key={e.check} className="flex flex-wrap items-start gap-2 text-sm">
            {confirmable(e) ? (
              <input type="checkbox" className="mt-1 h-4 w-4 accent-primary"
                aria-label={`I checked ${e.check} myself`}
                checked={confirmed.includes(e.check)} onChange={() => onToggle(e.check)} />
            ) : <span className="inline-block w-4" aria-hidden />}
            <span className="font-mono text-xs">{e.check}</span>
            <Badge variant={VARIANT[e.state]}>{LEDGER_LABEL[e.state]}</Badge>
            {e.method && e.method !== "unit" && <span className="text-xs text-muted-foreground">{e.method}</span>}
            {e.tests.length > 0 && (
              <span className="truncate font-mono text-xs text-muted-foreground" title={e.tests.join("\n")}>
                {e.tests[0]}{e.tests.length > 1 ? ` +${e.tests.length - 1}` : ""}
              </span>
            )}
            {e.detail && <span className="basis-full pl-6 text-xs text-muted-foreground">{e.detail}</span>}
          </li>
        ))}
      </ul>
      {entries.some(confirmable) && (
        <p className="m-0 mt-1 text-xs text-muted-foreground">
          Tick what you checked yourself; it is recorded as confirmed by you when you accept.
        </p>
      )}
    </details>
  );
}
