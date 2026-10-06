import { Card } from "../../components/ui/card";
import type { Routed } from "./overviewModel";

/** "Routed to you", grouped by the seats the user holds. Owner always shows. */
export function RoutedToYou({ routed }: { routed: Routed }) {
  const groups: { name: string; lines: string[]; empty: string }[] = [];
  if (routed.reviewer) groups.push({ name: "Reviewer", lines: routed.reviewer, empty: "No co-sign needed." });
  if (routed.lead) groups.push({ name: "Lead", lines: routed.lead, empty: "No decisions waiting." });
  groups.push({ name: "Owner", lines: routed.owner, empty: "Nothing to accept or answer." });
  return (
    <Card className="p-3" data-testid="routed-to-you">
      <h3 className="m-0 mb-2 text-sm font-semibold">Routed to you</h3>
      <div className="flex flex-col gap-2">
        {groups.map((g) => (
          <section key={g.name} aria-label={g.name}>
            <h4 className="m-0 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{g.name}</h4>
            {g.lines.length === 0 ? (
              <p className="m-0 text-xs text-muted-foreground">{g.empty}</p>
            ) : (
              <ul className="m-0 list-none p-0 text-sm">
                {g.lines.map((l) => <li key={l}>{l}</li>)}
              </ul>
            )}
          </section>
        ))}
      </div>
    </Card>
  );
}
