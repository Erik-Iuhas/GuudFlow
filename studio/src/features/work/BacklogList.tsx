import { useState, type FormEvent } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input, Label, Select, Textarea } from "../../components/ui/form-controls";
import { formatUsd } from "../../lib/cost";
import type { BacklogItemWire, NewItem } from "./api";
import { OUTCOME_LABEL, groupBacklog, itemEvents, movedPriority, parseAcceptance, parseItemIds, recentUpdate } from "./model";

// A disabled arrow just fades: no muted box at the ends of the list.
const ARROW = "disabled:border-transparent disabled:bg-transparent";

export interface BacklogListProps {
  items: BacklogItemWire[];
  selected: string[];
  onToggle: (itemId: string) => void;
  onMove: (itemId: string, priority: number) => void;
  onDrop: (itemId: string) => void;
  onReopen: (itemId: string) => void;
  onAdd: (item: NewItem) => void;
  onStart: () => void;
  adding?: boolean;
  starting?: boolean;
  /** The lifecycle phase an open item waits on ("waits for design"), if any. */
  phaseLabel?: (itemId: string) => string | null;
}

/**
 * The project backlog (plan §13): the work that outlives runs. Open items are in
 * priority order; a run takes the selected ones and the rest stay for later.
 */
export function BacklogList({
  items, selected, onToggle, onMove, onDrop, onReopen, onAdd, onStart, adding, starting, phaseLabel,
}: BacklogListProps) {
  const groups = groupBacklog(items);
  return (
    <section aria-label="Backlog" className="flex flex-col gap-4">
      {groups.inRun.length > 0 && (
        <div>
          <h3 className="mb-2 mt-0 text-sm font-semibold">In a run</h3>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {groups.inRun.map((item) => (
              <ItemRow key={item.id} item={item}>
                <Badge variant={item.pending ? "waiting" : "running"}>
                  {item.pending ? OUTCOME_LABEL[item.pending] : `in ${item.run_id}`}
                </Badge>
              </ItemRow>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="m-0 text-sm font-semibold">Open ({groups.open.length})</h3>
          <Button size="sm" onClick={onStart} disabled={selected.length === 0 || starting}>
            {starting ? "Starting…" : `Start session with ${selected.length} item${selected.length === 1 ? "" : "s"}`}
          </Button>
        </div>
        {groups.open.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">Nothing open. Add the next piece of work below.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {groups.open.map((item, i) => {
              const up = movedPriority(groups.open, item.id, "up");
              const down = movedPriority(groups.open, item.id, "down");
              return (
                <ItemRow
                  key={item.id}
                  item={item}
                  lead={
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 accent-primary"
                      aria-label={`Include ${item.id} in the next run`}
                      checked={selected.includes(item.id)}
                      onChange={() => onToggle(item.id)}
                    />
                  }
                >
                  {phaseLabel?.(item.id) && <Badge variant="outline">{phaseLabel(item.id)}</Badge>}
                  <Button variant="ghost" size="sm" className={ARROW} aria-label={`Move ${item.id} up`} disabled={up === null}
                    onClick={() => up !== null && onMove(item.id, up)}>↑</Button>
                  <Button variant="ghost" size="sm" className={ARROW} aria-label={`Move ${item.id} down`}
                    disabled={down === null || i === groups.open.length - 1}
                    onClick={() => down !== null && onMove(item.id, down)}>↓</Button>
                  <Button variant="ghost" size="sm" onClick={() => onDrop(item.id)}>Drop</Button>
                </ItemRow>
              );
            })}
          </ul>
        )}
      </div>

      <AddItemForm onAdd={onAdd} adding={adding} />

      {(groups.done.length > 0 || groups.dropped.length > 0) && (
        <details>
          <summary className="cursor-pointer text-sm text-muted-foreground">
            Done ({groups.done.length}) and dropped ({groups.dropped.length})
          </summary>
          <ul className="m-0 mt-2 flex list-none flex-col gap-2 p-0">
            {[...groups.done, ...groups.dropped].map((item) => (
              <ItemRow key={item.id} item={item} muted>
                <Badge variant={item.status === "done" ? "complete" : "outline"}>{item.status}</Badge>
                <Button variant="ghost" size="sm" onClick={() => onReopen(item.id)}>Reopen</Button>
              </ItemRow>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function ItemRow({
  item, lead, children, muted,
}: {
  item: BacklogItemWire;
  lead?: React.ReactNode;
  children?: React.ReactNode;
  muted?: boolean;
}) {
  const last = [...item.history].reverse().find((h) => typeof h.summary === "string" && h.summary);
  const events = itemEvents(item.history);
  const update = recentUpdate(item.history);
  return (
    <li className="flex gap-2 rounded-lg border border-border bg-card p-3 text-card-foreground" data-testid={`backlog-${item.id}`}>
      {lead}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">{item.id}</span>
          <span className={muted ? "text-muted-foreground" : "font-medium"}>{item.title}</span>
          <Badge variant="outline">{item.kind}</Badge>
          <Badge variant="outline">size {item.size}</Badge>
          {item.depends_on.length > 0 && (
            <span className="text-xs text-muted-foreground">after {item.depends_on.join(", ")}</span>
          )}
          {item.source.startsWith("run:") && (
            <span className="text-xs text-muted-foreground">from {item.source.slice(4)}</span>
          )}
          {(item.serves ?? []).length > 0 && (
            <span className="text-xs text-muted-foreground" data-testid={`serves-${item.id}`}>
              serves{" "}
              {(item.serves ?? []).map((r) => (
                <span key={r} className="mr-1 rounded border border-primary/40 px-1 font-mono text-primary">{r}</span>
              ))}
            </span>
          )}
          {(item.total_cost_usd ?? 0) > 0 && (
            <span className="text-xs text-muted-foreground">{formatUsd(item.total_cost_usd ?? 0)} spent</span>
          )}
        </div>
        {item.acceptance.length > 0 && (
          <ul className="m-0 mt-1 pl-4 text-sm text-muted-foreground">
            {item.acceptance.map((a) => {
              const m = a.match(/^(R\d+\.\d+):\s*(.*)$/s);
              return m ? (
                <li key={a}><span className="mr-1 font-mono text-xs">{m[1]}</span>{m[2]}</li>
              ) : <li key={a}>{a}</li>;
            })}
          </ul>
        )}
        {update && (
          <p className="m-0 mt-1 rounded-r border-l-2 border-l-primary bg-primary/10 px-2 py-1 text-xs" data-testid={`update-${item.id}`}>
            <span className="font-medium">Updated in {update.where}</span> ({update.what})
            {update.why && <>: {update.why}</>}
            {update.quote && <span className="text-muted-foreground"> · because <q className="italic">{update.quote}</q></span>}
          </p>
        )}
        {last && (
          <p className="m-0 mt-1 text-xs text-muted-foreground">
            Last run ({String(last.run_id)}): {String(last.summary)}
          </p>
        )}
        {events.length > 0 && (
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer text-muted-foreground">History ({events.length})</summary>
            <ol className="m-0 mt-1 grid list-none gap-1 p-0" data-testid={`history-${item.id}`}>
              {events.map((e, i) => (
                <li key={i} className="grid grid-cols-[6.5rem_1fr] gap-2">
                  <span className="font-mono text-muted-foreground">{e.where || "—"}</span>
                  <span>
                    {e.what}
                    {e.why && <span className="text-muted-foreground"> · {e.why}</span>}
                    {e.quote && <span className="text-muted-foreground"> · <q className="italic">{e.quote}</q></span>}
                  </span>
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
      <div className="flex shrink-0 items-start gap-1">{children}</div>
    </li>
  );
}

function AddItemForm({ onAdd, adding }: { onAdd: (item: NewItem) => void; adding?: boolean }) {
  const [title, setTitle] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [kind, setKind] = useState("code");
  const [size, setSize] = useState("M");
  const [after, setAfter] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    if (title.trim().length < 3) return;
    const dependsOn = parseItemIds(after);
    onAdd({ title: title.trim(), acceptance: parseAcceptance(acceptance), kind, size,
      ...(dependsOn.length ? { depends_on: dependsOn } : {}) });
    setTitle("");
    setAcceptance("");
    setAfter("");
  }

  return (
    <form onSubmit={submit} className="grid gap-2 rounded-lg border border-dashed border-border p-3" aria-label="Add a backlog item">
      <div>
        <Label htmlFor="backlog-title">New item</Label>
        <Input id="backlog-title" placeholder="e.g. Let users reset their password" value={title}
          onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div>
        <Label htmlFor="backlog-acceptance">Done when (one check per line)</Label>
        <Textarea id="backlog-acceptance" rows={3} value={acceptance}
          placeholder={"POST /reset sends an email\nThe link expires after an hour"}
          onChange={(e) => setAcceptance(e.target.value)} />
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <div>
          <Label htmlFor="backlog-kind">Kind</Label>
          <Select id="backlog-kind" value={kind} onChange={(e) => setKind(e.target.value)} className="w-32">
            {["code", "test", "docs", "research", "visual"].map((k) => <option key={k} value={k}>{k}</option>)}
          </Select>
        </div>
        <div>
          <Label htmlFor="backlog-size">Size</Label>
          <Select id="backlog-size" value={size} onChange={(e) => setSize(e.target.value)} className="w-24">
            {["S", "M", "L"].map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </div>
        <div>
          <Label htmlFor="backlog-after">After (items)</Label>
          <Input id="backlog-after" placeholder="B2, B5" value={after} className="w-32"
            onChange={(e) => setAfter(e.target.value)} />
        </div>
        <Button type="submit" size="sm" disabled={adding || title.trim().length < 3}>
          {adding ? "Adding…" : "Add to backlog"}
        </Button>
      </div>
    </form>
  );
}
