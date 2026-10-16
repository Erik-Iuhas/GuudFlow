import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Markdown } from "../../lib/markdown";
import { fetchBrief, fetchBriefs } from "./api";

/** Every brief a worker was given this run (rules files, skills offered and the brief), newest
 * first. `card` narrows to one card's turns (the card drawer). */
export function WhatItWasTold({ runId, role, card, live = false }: {
  runId: string;
  role: string;
  card?: string;
  live?: boolean;
}) {
  const list = useQuery({
    queryKey: ["briefs", runId] as const,
    queryFn: () => fetchBriefs(runId),
    refetchInterval: live ? 10_000 : false,
  });
  const entries = (list.data?.roles[role] ?? []).filter((e) => !card || e.card === card).slice().reverse();
  const [picked, setPicked] = useState<number | null>(null);
  const n = picked ?? entries[0]?.n ?? null;
  const one = useQuery({
    queryKey: ["briefs", runId, role, n] as const,
    queryFn: () => fetchBrief(runId, role, n!),
    enabled: n !== null,
    staleTime: Infinity,
  });
  if (list.isPending) return <p className="m-0 p-3 text-sm text-muted-foreground" role="status">Loading…</p>;
  if (list.isError) return <p className="m-0 p-3 text-sm text-destructive" role="alert">Could not load: {list.error.message}</p>;
  if (entries.length === 0) {
    return <p className="m-0 p-3 text-sm text-muted-foreground">Nothing recorded yet{card ? " for this card" : ""}.</p>;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-3" data-testid={`briefs-${role}`}>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        Turn
        <select aria-label="Turn" className="h-7 rounded-md border border-input bg-background px-2 text-xs"
                value={n ?? ""} onChange={(e) => setPicked(Number(e.target.value))}>
          {entries.map((e) => (
            <option key={e.n} value={e.n}>
              {e.n} · {e.card} · {new Date(e.at * 1000).toLocaleTimeString()}
            </option>
          ))}
        </select>
      </label>
      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-border p-3">
        {one.isPending ? <p className="m-0 text-sm text-muted-foreground" role="status">Loading…</p>
          : one.isError ? <p className="m-0 text-sm text-destructive" role="alert">{one.error.message}</p>
          : <Markdown text={one.data.text} className="break-words text-sm" />}
      </div>
    </div>
  );
}
