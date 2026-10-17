import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRunCards, useRunMeta } from "../../api/queries";
import { useSpec, type SpecWire } from "../plan/api";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/utils";
import { useRunStore, useRunStoreApi } from "../../stores/runStoreContext";
import { AddCardDialog } from "./AddCardDialog";
import { CardDrawer } from "./CardDrawer";
import { CardLinks, CardTile } from "./CardTile";
import {
  COLUMNS,
  DEFAULT_REVIEW_CAP,
  blockedOn,
  buildLanes,
  columnCounts,
  computeReorder,
  reasonGroups,
  COLUMN_LABEL,
} from "./boardModel";
import type { CardView } from "../../stores/runEvents";
import { useAddCard, useSetPriority } from "./useCardMutations";

interface Props {
  runId: string;
  /** Review column WIP cap; defaults to the backend's `BoardLimits.review_column_cap`. */
  reviewCap?: number;
  className?: string;
}

/** Re-render every `ms` so "age in column" stays current. */
function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

const cellKey = (role: string, column: string): string => `${role}::${column}`;

/**
 * The run's task board: role swimlanes by column (docs/OPENCODE_HARNESS_PLAN.md 4.6).
 *
 * Reads the run store's `cards` slice (seeded from `GET /cards`, moved by TASK_* signals).
 * Must be mounted under a `RunStoreProvider` and a `QueryClientProvider`. The UI never
 * changes a card's column: dragging a card only reorders it inside its cell, via PATCH priority.
 */
export function TaskBoard({ runId, reviewCap: capProp, className }: Props) {
  const store = useRunStoreApi();
  const meta = useRunMeta(runId);
  const reviewCap = capProp ?? meta.data?.review_column_cap ?? DEFAULT_REVIEW_CAP;
  const cardsQuery = useRunCards(runId);
  const cards = useRunStore((s) => s.cards);
  const agents = useRunStore((s) => s.agents);
  const names = useRunStore((s) => s.names);
  const live = useRunStore((s) => s.live);
  const stubCount = useRunStore((s) => Object.values(s.cards).filter((c) => c.stub).length);
  const setPriority = useSetPriority(runId);
  const now = useNow();

  const projectId = meta.data?.project_id ?? "";
  const spec = useSpec(projectId, !!projectId).data;
  const [view, setView] = useState<"role" | "requirement">("role");
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<{ owner?: string; title?: string }>({});
  const [notice, setNotice] = useState<string | null>(null);
  // Optimistic priorities for in-flight PATCHes. Rolled back on error, cleared on success.
  const [pending, setPending] = useState<Record<string, number>>({});

  const { data, refetch } = cardsQuery;
  useEffect(() => {
    if (data) store.getState().seedCards(data);
  }, [data, store]);

  // A TASK_ASSIGNED stub only knows its title: refetch for kind, size and acceptance.
  useEffect(() => {
    if (stubCount > 0) void refetch();
  }, [stubCount, refetch]);

  const priorityOf = useCallback(
    (c: { id: string; priority: number }): number => pending[c.id] ?? c.priority,
    [pending],
  );
  const list = useMemo(() => Object.values(cards), [cards]);
  const roles = useMemo(() => {
    // The team from the TEAM signal too, so cards can be added before anyone has started.
    const all = [...Object.keys(agents), ...Object.keys(names), ...list.map((c) => c.owner)]
      .filter((r) => Boolean(r) && !r.includes("overseer"));
    return [...new Set(all)];
  }, [agents, names, list]);
  const lanes = useMemo(() => buildLanes(list, roles, priorityOf), [list, roles, priorityOf]);
  const counts = useMemo(() => columnCounts(list), [list]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const reorder = useCallback(
    async (role: string, column: (typeof COLUMNS)[number]["id"], cardId: string, toIndex: number) => {
      const lane = lanes.find((l) => l.role === role);
      const ordered = (lane?.cells[column] ?? []).map((c) => ({ id: c.id, priority: priorityOf(c) }));
      const changes = computeReorder(ordered, cardId, toIndex);
      const ids = Object.keys(changes);
      if (ids.length === 0) return;
      setNotice(null);
      setPending((p) => ({ ...p, ...changes }));
      const results = await Promise.allSettled(
        ids.map((id) => setPriority.mutateAsync({ taskId: id, priority: changes[id] as number })),
      );
      // Success seeded the store with the server's priority; failure rolls back to the old one.
      setPending((p) => {
        const next = { ...p };
        for (const id of ids) delete next[id];
        return next;
      });
      const failed = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
      if (failed) {
        const msg = failed.reason instanceof Error ? failed.reason.message : String(failed.reason);
        setNotice(`Could not change priority: ${msg}`);
      }
    },
    [lanes, priorityOf, setPriority],
  );

  const onDragEnd = useCallback(
    ({ active, over }: DragEndEvent) => {
      if (!over || active.id === over.id) return;
      const from = active.data.current?.["cellId"];
      const to = over.data.current?.["cellId"];
      // A drop in another cell would mean a column (or lane) change, which only the scheduler does.
      if (typeof from !== "string" || from !== to) return;
      const [role, column] = from.split("::") as [string, (typeof COLUMNS)[number]["id"]];
      const toIndex = over.data.current?.["sortable"]?.index;
      if (typeof toIndex === "number") void reorder(role, column, String(active.id), toIndex);
    },
    [reorder],
  );

  const loading = cardsQuery.isPending && list.length === 0;
  const loadError = cardsQuery.isError ? cardsQuery.error : null;

  return (
    <section aria-label="Task board" className={cn("flex min-w-0 flex-col gap-3", className)}>
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="m-0 mr-auto text-base font-semibold">Task board</h2>
        <span className="text-xs text-muted-foreground">{`${list.length} cards`}</span>
        <div role="group" aria-label="Group cards" className="inline-flex overflow-hidden rounded-md border border-border text-xs">
          {(["role", "requirement"] as const).map((v) => (
            <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}
              className={cn("border-0 px-2 py-1", view === v ? "bg-accent text-foreground" : "bg-transparent text-muted-foreground")}>
              {v === "role" ? "By role" : "By requirement"}
            </button>
          ))}
        </div>
        <Button
          size="sm"
          onClick={() => { setDraft({}); setAdding(true); }}
          disabled={!live}
          title={live ? undefined : "This run has finished: the board is read-only"}
        >
          Add card
        </Button>
      </header>

      {notice && (
        <p role="alert" className="m-0 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {notice}
        </p>
      )}

      {loadError && (
        <div role="alert" className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <span className="mr-auto">{`Could not load the cards: ${loadError.message}`}</span>
          <Button size="sm" variant="outline" onClick={() => void refetch()}>Retry</Button>
        </div>
      )}

      {loading ? (
        <p className="m-0 text-sm text-muted-foreground">Loading cards...</p>
      ) : list.length === 0 ? (
        !loadError && (
          <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
            <p className="m-0 font-medium text-foreground">The planner hasn't produced cards yet</p>
            <p className="m-0 mt-1">Cards appear here as soon as the plan is validated.</p>
          </div>
        )
      ) : view === "requirement" ? (
        <ByRequirement cards={list} spec={spec} onOpen={setOpenId} />
      ) : (
        <div className="overflow-x-auto">
          <div className="min-w-[72rem]">
            <div className="sticky top-0 z-10 mb-2 grid grid-cols-6 gap-2 bg-background">
              {COLUMNS.map((col) => {
                const count = counts[col.id];
                const atCap = col.id === "review" && count >= reviewCap;
                return (
                  <div
                    key={col.id}
                    data-testid={`col-${col.id}`}
                    className={cn(
                      "flex items-center gap-1 rounded-md border border-t-4 border-border bg-card px-2 py-1 text-sm font-semibold",
                      col.accent,
                    )}
                  >
                    <span className="mr-auto">{col.label}</span>
                    {col.id === "review" ? (
                      <>
                        <span aria-label={`${count} of ${reviewCap} review slots used`}>{`${count}/${reviewCap}`}</span>
                        {atCap && <Badge variant="blocked">at cap</Badge>}
                      </>
                    ) : (
                      <span aria-label={`${count} cards`}>{count}</span>
                    )}
                  </div>
                );
              })}
            </div>

            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
              {lanes.map((lane) => (
                <div
                  key={lane.role}
                  role="region"
                  aria-label={`Lane ${lane.role}`}
                  className="mb-3 grid gap-1"
                >
                  <div className="flex items-center gap-2">
                    <h3 className="m-0 text-sm font-semibold text-muted-foreground">{lane.role}</h3>
                    {live && (
                      <QuickAdd runId={runId} role={lane.role}
                        onMore={(title) => { setDraft({ owner: lane.role, title }); setAdding(true); }} />
                    )}
                  </div>
                  <div className="grid grid-cols-6 gap-2">
                    {COLUMNS.map((col) => {
                      const cell = lane.cells[col.id];
                      const id = cellKey(lane.role, col.id);
                      return (
                        <ul
                          key={col.id}
                          aria-label={`${lane.role} ${col.label}`}
                          className="m-0 flex min-h-12 flex-col gap-2 rounded-md bg-muted/40 p-1.5"
                        >
                          <SortableContext
                            id={id}
                            items={cell.map((c) => c.id)}
                            strategy={verticalListSortingStrategy}
                          >
                            {cell.map((card, index) => (
                              <CardTile
                                key={card.id}
                                card={card}
                                priority={priorityOf(card)}
                                index={index}
                                count={cell.length}
                                cellId={id}
                                sortable={live && col.id !== "done"}
                                now={now}
                                blocked={blockedOn(card, cards)}
                                onOpen={setOpenId}
                                onMove={(cardId, toIndex) => void reorder(lane.role, col.id, cardId, toIndex)}
                              />
                            ))}
                          </SortableContext>
                        </ul>
                      );
                    })}
                  </div>
                </div>
              ))}
            </DndContext>
          </div>
        </div>
      )}

      <CardDrawer runId={runId} cardId={openId} onClose={() => setOpenId(null)} spec={spec} projectId={projectId} />
      <AddCardDialog
        runId={runId}
        roles={roles}
        open={adding}
        onOpenChange={setAdding}
        spec={spec}
        cards={list}
        initialOwner={draft.owner}
        initialTitle={draft.title}
      />
    </section>
  );
}

/**
 * A lane's quick-add row (worker context plan F2): type a title and press Enter to add a small
 * card whose acceptance is the title; "More" opens the full dialog prefilled.
 */
function QuickAdd({ runId, role, onMore }: { runId: string; role: string; onMore: (title: string) => void }) {
  const add = useAddCard(runId);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    const text = title.trim();
    if (text.length < 5) return setError("At least 5 characters.");
    setError(null);
    try {
      await add.mutateAsync({ owner: role, title: text, acceptance: [text], kind: "code", size: "S" });
      setTitle("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the card.");
    }
  }

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1">
      <input
        className="h-7 w-64 rounded-md border border-input bg-background px-2 text-xs"
        aria-label={`Add a card for ${role}`}
        placeholder={`Add a card for ${role}...`}
        value={title}
        disabled={add.isPending}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void submit();
          }
        }}
      />
      <button type="button" className="border-0 bg-transparent text-xs text-muted-foreground underline"
        aria-label={`More options for a ${role} card`} onClick={() => onMore(title.trim())}>
        More
      </button>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </div>
  );
}

/** Cards grouped by the requirement they serve (plan §12), with how many of its checks are done. */
function ByRequirement({ cards, spec, onOpen }: {
  cards: CardView[];
  spec: SpecWire | undefined;
  onOpen: (id: string) => void;
}) {
  const titles = Object.fromEntries((spec?.requirements ?? []).map((r) => [r.id, r.title || r.text]));
  const groups = reasonGroups(cards, titles);
  return (
    <div className="grid gap-3" data-testid="by-requirement">
      {groups.map((g) => {
        const done = g.cards.filter((c) => c.column === "done").length;
        return (
          <section key={g.key} aria-label={g.key === "support" || g.key === "none" ? g.title : `${g.key} ${g.title}`}
            className={cn("grid gap-2 rounded-md border border-border p-3", g.key === "none" && "border-dashed")}>
            <h3 className="m-0 flex flex-wrap items-baseline gap-2 text-sm font-semibold">
              {g.key !== "support" && g.key !== "none" && (
                <span className="rounded border border-primary/40 px-1.5 font-mono text-xs text-primary">{g.key}</span>
              )}
              <span>{g.title || g.key}</span>
              <span className="text-xs font-normal text-muted-foreground">
                {done} of {g.cards.length} card{g.cards.length === 1 ? "" : "s"} done
                {g.checks.length > 0 && ` · checks ${g.checks.join(", ")}`}
              </span>
            </h3>
            <ul className="m-0 grid list-none gap-2 p-0 sm:grid-cols-2 xl:grid-cols-3">
              {g.cards.map((c) => (
                <li key={c.id}>
                  <button type="button" onClick={() => onOpen(c.id)}
                    className={cn("w-full rounded-md border border-border bg-card p-2 text-left text-sm",
                      c.column === "done" && "border-status-complete/50")}>
                    <span className="mr-1 font-mono text-xs text-muted-foreground">{c.id}</span>
                    <span className="font-medium">{c.title}</span>
                    <span className="block text-xs text-muted-foreground">{COLUMN_LABEL[c.column]} · {c.owner}</span>
                    <CardLinks card={c} />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
