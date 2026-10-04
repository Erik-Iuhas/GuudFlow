import { useMemo } from "react";
import { Badge } from "../../components/ui/badge";
import type { TaskColumn } from "../../lib/runEvents.gen";
import { cn } from "../../lib/utils";
import { useRunStore } from "../../stores/runStoreContext";
import { COLUMN_CLASS, COLUMN_LABEL } from "./AgentLane";

const COLUMN_ORDER: TaskColumn[] = ["needs_attention", "blocked", "in_progress", "review", "todo", "done"];

/** The run's cards, grouped by column. The full board lives in the board feature (U2). */
export function CardsPanel() {
  const cards = useRunStore((s) => s.cards);
  const groups = useMemo(() => {
    const by = new Map<TaskColumn, (typeof cards)[string][]>();
    for (const c of Object.values(cards)) {
      const list = by.get(c.column) ?? [];
      list.push(c);
      by.set(c.column, list);
    }
    return COLUMN_ORDER.filter((col) => by.has(col)).map((col) => ({
      column: col,
      cards: (by.get(col) ?? []).sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true })),
    }));
  }, [cards]);

  if (groups.length === 0) {
    return <p className="m-0 p-3 text-sm text-muted-foreground">No cards yet. The planner creates them from its plan.</p>;
  }
  return (
    <div className="flex flex-col gap-3 p-2" data-testid="cards-panel">
      {groups.map((g) => (
        <section key={g.column} aria-label={COLUMN_LABEL[g.column]}>
          <h4 className={cn("m-0 mb-1 text-xs font-semibold uppercase tracking-wide", COLUMN_CLASS[g.column].split(" ")[1])}>
            {COLUMN_LABEL[g.column]} ({g.cards.length})
          </h4>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {g.cards.map((c) => (
              <li key={c.id} className="flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1 text-sm">
                <Badge variant="outline" className={cn("shrink-0", COLUMN_CLASS[c.column])}>
                  {c.id}
                </Badge>
                <span className="min-w-0 flex-1 truncate" title={c.title}>
                  {c.title}
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">{c.owner}</span>
                {c.round > 0 && <span className="shrink-0 text-xs text-muted-foreground">r{c.round}</span>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
