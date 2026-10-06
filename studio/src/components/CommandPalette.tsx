import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { runsApi } from "../api";
import type { RunMeta } from "../types";
import { cn } from "../lib/utils";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";

interface PaletteItem {
  id: string;
  label: string;
  hint: string;
  to: string;
}

/** Every top-level route, in nav order. Tournaments are started from the Training view. */
export const PALETTE_ROUTES: PaletteItem[] = [
  { id: "route-dashboard", label: "Dashboard", hint: "Go to", to: "/" },
  { id: "route-projects", label: "Projects", hint: "Go to", to: "/projects" },
  { id: "route-oneshot", label: "One-shot", hint: "Go to", to: "/oneshot" },
  { id: "route-new-project", label: "New project", hint: "Go to", to: "/projects/new" },
  { id: "route-roster", label: "Roster", hint: "Go to", to: "/roster" },
  { id: "route-training", label: "Training", hint: "Go to", to: "/training" },
  { id: "route-tournament", label: "Tournament", hint: "Go to", to: "/training" },
  { id: "route-settings", label: "Settings", hint: "Go to", to: "/settings" },
];

const MAX_RUNS = 6;

/** The most recently created runs first. Runs without a timestamp keep API order after dated ones. */
export function lastRuns(runs: RunMeta[], limit = MAX_RUNS): RunMeta[] {
  return [...runs]
    .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
    .slice(0, limit);
}

/** Case-insensitive substring match on label and hint. */
export function filterItems(items: PaletteItem[], query: string): PaletteItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return items;
  return items.filter((i) => `${i.label} ${i.hint}`.toLowerCase().includes(q));
}

/**
 * Keyboard command palette: Cmd/Ctrl+K opens it from anywhere, arrows move, Enter opens.
 * Lists the routes and the last opened runs (loaded when it opens; a failed load only hides them).
 */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    let cancelled = false;
    runsApi
      .list()
      .then((r) => !cancelled && setRuns(lastRuns(r)))
      .catch(() => !cancelled && setRuns([]));
    return () => {
      cancelled = true;
    };
  }, [open]);

  const items = useMemo(() => {
    const runItems: PaletteItem[] = runs.map((r) => ({
      id: `run-${r.run_id}`,
      label: r.run_id,
      hint: r.goal ? `Run · ${r.goal}` : `Run · ${r.project_id}`,
      to: `/runs/${r.run_id}`,
    }));
    return filterItems([...PALETTE_ROUTES, ...runItems], query);
  }, [runs, query]);

  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(items.length - 1, 0)));
  }, [items.length]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [active, items]);

  function choose(item: PaletteItem | undefined) {
    if (!item) return;
    onOpenChange(false);
    navigate(item.to);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (items.length ? (a + 1) % items.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (items.length ? (a - 1 + items.length) % items.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(items[active]);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="top-[20%] max-w-xl translate-y-0 gap-0 p-0" onKeyDown={onKeyDown}>
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription className="sr-only">
          Type to filter routes and recent runs. Use the arrow keys and Enter to open one.
        </DialogDescription>
        <input
          autoFocus
          role="combobox"
          aria-expanded
          aria-controls="palette-list"
          aria-activedescendant={items[active] ? `palette-${items[active].id}` : undefined}
          aria-label="Search routes and runs"
          placeholder="Go to a page or run…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          className="block w-full rounded-b-none rounded-t-lg border-0 border-b border-border bg-transparent px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none"
        />
        <ul
          id="palette-list"
          ref={listRef}
          role="listbox"
          aria-label="Results"
          className="m-0 max-h-80 list-none overflow-y-auto p-1"
        >
          {items.length === 0 && (
            <li className="px-3 py-6 text-center text-sm text-muted-foreground" role="presentation">
              Nothing matches “{query}”.
            </li>
          )}
          {items.map((item, i) => (
            <li
              key={item.id}
              id={`palette-${item.id}`}
              role="option"
              aria-selected={i === active}
              onMouseMove={() => setActive(i)}
              onClick={() => choose(item)}
              className={cn(
                "flex cursor-pointer items-center justify-between gap-3 rounded-md px-3 py-2 text-sm",
                i === active ? "bg-accent text-accent-foreground" : "text-foreground",
              )}
            >
              <span className={cn("truncate", item.id.startsWith("run-") && "font-mono")}>{item.label}</span>
              <span className="truncate text-xs text-muted-foreground">{item.hint}</span>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
