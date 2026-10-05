import { Badge } from "../../components/ui/badge";
import { Card } from "../../components/ui/card";
import { cn } from "../../lib/utils";
import type { PlaybookEntry } from "../../types";

export type DiffKind = "add" | "change" | "warn";

export interface DiffLine {
  kind: DiffKind;
  marker: "+" | "~" | "!";
  text: string;
}

function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** One diff-style line per playbook entry, e.g. "+ brief addendum for builder: ...". */
export function describeEntry(e: PlaybookEntry): DiffLine {
  const v = valueText(e.value);
  switch (e.section) {
    case "brief_addenda":
      return { kind: "add", marker: "+", text: `brief addendum for ${e.key}: ${v}` };
    case "task_templates":
      return { kind: "add", marker: "+", text: `task template ${e.key}: ${v}` };
    case "review_rounds":
      return { kind: "change", marker: "~", text: `review rounds ${e.key.replace(">", "→")}: ${v}` };
    case "capability_hints":
      return { kind: "change", marker: "~", text: `capability hint for ${e.key}: ${v}` };
    case "team_adjustments":
      return { kind: "change", marker: "~", text: `team adjustment ${e.key}: ${v}` };
    case "known_pitfalls":
      return { kind: "warn", marker: "!", text: `known pitfall ${e.key}: ${v}` };
  }
}

const KIND_CLASS: Record<DiffKind, string> = {
  add: "border-status-complete/40 bg-status-complete/10",
  change: "border-status-waiting/40 bg-status-waiting/10",
  warn: "border-status-blocked/40 bg-status-blocked/10",
};

/**
 * Lessons from earlier runs that shaped this proposal, as a diff-style list. This panel
 * only informs: nothing is applied to the team until the user approves it.
 */
export function PlaybookPanel({ entries }: { entries: PlaybookEntry[] }) {
  return (
    <Card className="flex flex-col gap-2 p-3" data-testid="playbook-panel">
      <h3 className="m-0 text-sm font-semibold">Playbook</h3>
      <p className="m-0 text-xs text-muted-foreground">
        Lessons from earlier runs of this project shaped this proposal. Nothing is applied until
        you approve the team.
      </p>
      {entries.length === 0 ? (
        <p className="m-0 text-xs text-muted-foreground">No playbook entries were used.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {entries.map((e) => {
            const line = describeEntry(e);
            return (
              <li
                key={e.id}
                data-testid="playbook-entry"
                className={cn("rounded-md border p-2 text-xs", KIND_CLASS[line.kind])}
              >
                <div className="font-mono">
                  <span aria-hidden className="font-bold">{line.marker} </span>
                  {line.text}
                </div>
                <div className="mt-1 flex gap-1.5">
                  <Badge variant="outline">confidence {Math.round(e.confidence * 100)}%</Badge>
                  <Badge variant="outline">
                    {e.evidence_runs.length} {e.evidence_runs.length === 1 ? "run" : "runs"}
                  </Badge>
                  {e.needs_review && <Badge variant="blocked">needs review</Badge>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
