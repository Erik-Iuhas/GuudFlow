import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Badge } from "../../components/ui/badge";
import { cn } from "../../lib/utils";
import type { CardView } from "../../stores/runEvents";
import { formatAge, isAged, type BlockedOn } from "./boardModel";

interface Props {
  card: CardView;
  /** Priority shown and sorted on (the optimistic value while a PATCH is in flight). */
  priority: number;
  /** Position inside the lane cell, for the raise/lower buttons. */
  index: number;
  count: number;
  /** Cell id (`role::column`): a drop only reorders when it lands in the same cell. */
  cellId: string;
  /** Done cards cannot be reprioritised (the backend refuses), and a finished run is read-only. */
  sortable: boolean;
  now: number;
  blocked: BlockedOn | null;
  onOpen: (id: string) => void;
  onMove: (id: string, toIndex: number) => void;
}

/** One card on the board. Dragging only reorders it within its cell; it never changes column. */
export function CardTile({
  card, priority, index, count, cellId, sortable, now, blocked, onOpen, onMove,
}: Props) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: card.id, disabled: !sortable, data: { cellId } });
  const age = formatAge(card.since, now);
  const aged = isAged(card, now);
  const attention = card.column === "needs_attention";

  return (
    <li
      ref={setNodeRef}
      // dnd-kit hands back a computed transform/transition per frame; it cannot be a class.
      style={{ transform: CSS.Transform.toString(transform), transition }}
      data-testid={`card-${card.id}`}
      data-column={card.column}
      className={cn(
        "list-none rounded-md border border-border bg-card p-2 text-card-foreground shadow-sm",
        attention && "ring-2 ring-destructive",
        isDragging && "z-10 opacity-80 shadow-lg",
      )}
    >
      <div className="flex items-start gap-1">
        {sortable && (
          <button
            type="button"
            ref={setActivatorNodeRef}
            aria-label={`Drag ${card.id} to reprioritise`}
            title="Drag to reprioritise (Space to lift, arrows to move)"
            className="cursor-grab touch-none rounded-sm border-0 bg-transparent px-1 text-muted-foreground hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            {...attributes}
            {...listeners}
          >
            <span aria-hidden>⠿</span>
          </button>
        )}
        <button
          type="button"
          onClick={() => onOpen(card.id)}
          className="min-w-0 flex-1 rounded-sm border-0 bg-transparent p-0 text-left text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="mr-1 font-mono text-xs text-muted-foreground">{card.id}</span>
          <span className="font-medium">{card.title}</span>
        </button>
        {sortable && count > 1 && (
          <span className="flex flex-col">
            <button
              type="button"
              aria-label={`Raise priority of ${card.id}`}
              disabled={index === 0}
              onClick={() => onMove(card.id, index - 1)}
              className="rounded-sm border-0 bg-transparent px-1 text-xs leading-none text-muted-foreground hover:bg-accent disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span aria-hidden>▲</span>
            </button>
            <button
              type="button"
              aria-label={`Lower priority of ${card.id}`}
              disabled={index === count - 1}
              onClick={() => onMove(card.id, index + 1)}
              className="rounded-sm border-0 bg-transparent px-1 text-xs leading-none text-muted-foreground hover:bg-accent disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span aria-hidden>▼</span>
            </button>
          </span>
        )}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        <span className="text-xs text-muted-foreground" data-testid={`owner-${card.id}`}>
          {card.owner}
        </span>
        {card.kind && <Badge variant="outline">{card.kind}</Badge>}
        {card.size && <Badge variant="secondary">{card.size}</Badge>}
        {priority !== 0 && <Badge variant="outline">{`P${priority}`}</Badge>}
        {card.round > 0 && <Badge variant="waiting">{`round ${card.round}`}</Badge>}
        {attention && <Badge variant="failed">Attention</Badge>}
        {blocked && (
          <Badge variant="blocked">
            {["blocked on", blocked.role, blocked.card].filter(Boolean).join(" ")}
          </Badge>
        )}
      </div>

      {age && (
        <div
          className={cn("mt-1 text-xs", aged ? "font-medium text-status-blocked" : "text-muted-foreground")}
          title={aged ? "Longer in this column than its size allows" : undefined}
        >
          {age} in column{aged ? " (slow)" : ""}
        </div>
      )}
    </li>
  );
}
