import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Badge } from "../../components/ui/badge";
import { cn } from "../../lib/utils";
import { useRunStore } from "../../stores/runStoreContext";
import { ArtifactViewer } from "./ArtifactViewer";
import { KindIcon, kindLabel } from "./KindIcon";
import {
  NO_TASK,
  VIRTUALIZE_THRESHOLD,
  buildItems,
  filterItems,
  formatBytes,
  isImageItem,
  type GalleryItem,
} from "./model";

const TILE_MIN_PX = 176;
const GAP_PX = 8;
/** Tile height (`h-44`) plus the row gap: the virtual row size. */
const ROW_PX = 176 + GAP_PX;
const COLUMN_CLASSES = [
  "grid-cols-1", "grid-cols-1", "grid-cols-2", "grid-cols-3", "grid-cols-4",
  "grid-cols-5", "grid-cols-6", "grid-cols-7", "grid-cols-8",
] as const;
const MAX_COLUMNS = COLUMN_CLASSES.length - 1;

function columnsFor(width: number): number {
  return Math.min(MAX_COLUMNS, Math.max(1, Math.floor((width + GAP_PX) / (TILE_MIN_PX + GAP_PX))));
}

function ageLabel(item: GalleryItem): string {
  return item.ts ? new Date(item.ts).toLocaleString() : "";
}

function Tile({ item, taskTitle, onOpen }: { item: GalleryItem; taskTitle: string | null; onOpen: () => void }) {
  return (
    <button
      type="button"
      data-testid="artifact-tile"
      onClick={onOpen}
      aria-label={`Open ${item.name}, ${kindLabel(item.kind)}`}
      className="flex h-44 w-full flex-col overflow-hidden rounded-lg border border-border bg-card p-0 text-left text-card-foreground hover:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex h-28 w-full items-center justify-center overflow-hidden border-b border-border bg-muted text-muted-foreground">
        {isImageItem(item) ? (
          <img src={item.url} alt="" loading="lazy" decoding="async" className="h-full w-full object-cover" />
        ) : (
          <KindIcon kind={item.kind} className="h-10 w-10" />
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5 p-2 text-xs">
        <span className="truncate text-sm font-medium" title={item.path}>{item.name}</span>
        <span className="flex items-center gap-1.5 text-muted-foreground">
          <KindIcon kind={item.kind} className="h-3.5 w-3.5 shrink-0" />
          <span>{kindLabel(item.kind)}</span>
          <span aria-hidden>·</span>
          <span>{formatBytes(item.size)}</span>
        </span>
        <span className="truncate text-muted-foreground" title={ageLabel(item)}>
          {[item.role, taskTitle].filter(Boolean).join(" · ") || "no role or card"}
        </span>
      </div>
    </button>
  );
}

/** Columns for the virtual grid, from the scroll container's width. */
function useColumns(ref: React.RefObject<HTMLDivElement>): number {
  const [columns, setColumns] = useState(4);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const width = el.clientWidth;
      if (width > 0) setColumns(columnsFor(width));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return columns;
}

function VirtualGrid({
  items,
  titleOf,
  onOpen,
}: {
  items: readonly GalleryItem[];
  titleOf: (item: GalleryItem) => string | null;
  onOpen: (item: GalleryItem) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const columns = useColumns(scrollRef);
  const rows = Math.ceil(items.length / columns);
  const virtualizer = useVirtualizer({
    count: rows,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_PX,
    overscan: 3,
  });

  return (
    <div ref={scrollRef} data-testid="artifact-virtual-scroller" className="h-[70vh] overflow-auto">
      <div
        className="relative w-full"
        // The total height is the virtualizer's measurement, not a design value.
        style={{ height: virtualizer.getTotalSize() }}
      >
        {virtualizer.getVirtualItems().map((row) => (
          <div
            key={row.key}
            className={cn("absolute left-0 top-0 grid w-full gap-2", COLUMN_CLASSES[columns])}
            // Each row is positioned at its measured offset.
            style={{ transform: `translateY(${row.start}px)` }}
          >
            {items.slice(row.index * columns, (row.index + 1) * columns).map((item) => (
              <Tile key={item.id} item={item} taskTitle={titleOf(item)} onOpen={() => onOpen(item)} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

const SELECT_CLASS =
  "rounded-md border border-input bg-background px-2 py-1 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * Every visual of a run (ARTIFACT and SCREENSHOT_READY signals from the run store), newest
 * first, filterable by card and role. Must sit under a `RunStoreProvider`.
 */
export function ArtifactGallery({ runId, className }: { runId: string; className?: string }) {
  const artifacts = useRunStore((s) => s.artifacts);
  const cards = useRunStore((s) => s.cards);
  const [task, setTask] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const items = useMemo(() => buildItems(artifacts, runId), [artifacts, runId]);
  const visible = useMemo(() => filterItems(items, { task, role }), [items, task, role]);
  const taskIds = useMemo(
    () => Array.from(new Set(items.map((i) => i.taskId).filter((t): t is string => t !== null))).sort(),
    [items],
  );
  const roles = useMemo(
    () => Array.from(new Set(items.map((i) => i.role).filter((r): r is string => r !== null))).sort(),
    [items],
  );
  const hasUntasked = items.some((i) => i.taskId === null);
  const titleOf = (item: GalleryItem): string | null =>
    item.taskId ? (cards[item.taskId]?.title ? `${item.taskId} ${cards[item.taskId].title}` : item.taskId) : null;
  const opened = openId ? items.find((i) => i.id === openId) ?? null : null;
  const virtualize = visible.length > VIRTUALIZE_THRESHOLD;

  return (
    <section aria-label="Artifact gallery" className={cn("flex flex-col gap-3", className)}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Task
          <select
            className={SELECT_CLASS}
            value={task ?? ""}
            onChange={(e) => setTask(e.target.value || null)}
          >
            <option value="">All tasks</option>
            {hasUntasked && <option value={NO_TASK}>No task</option>}
            {taskIds.map((id) => (
              <option key={id} value={id}>
                {cards[id]?.title ? `${id} · ${cards[id].title}` : id}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Role
          <select
            className={SELECT_CLASS}
            value={role ?? ""}
            onChange={(e) => setRole(e.target.value || null)}
          >
            <option value="">All roles</option>
            {roles.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </label>
        <Badge variant="outline" data-testid="artifact-count">
          {visible.length === items.length ? `${items.length} artifacts` : `${visible.length} of ${items.length} artifacts`}
        </Badge>
        {virtualize && <span className="text-xs text-muted-foreground">Long list: showing only the rows in view.</span>}
      </div>

      {items.length === 0 ? (
        <p className="m-0 rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          No artifacts yet. Screenshots, diagrams, charts and HTML pages agents make will appear here.
        </p>
      ) : visible.length === 0 ? (
        <p className="m-0 rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          Nothing matches these filters.
        </p>
      ) : virtualize ? (
        <VirtualGrid items={visible} titleOf={titleOf} onOpen={(item) => setOpenId(item.id)} />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-2">
          {visible.map((item) => (
            <Tile key={item.id} item={item} taskTitle={titleOf(item)} onOpen={() => setOpenId(item.id)} />
          ))}
        </div>
      )}

      <ArtifactViewer runId={runId} item={opened} allItems={items} onClose={() => setOpenId(null)} />
    </section>
  );
}
