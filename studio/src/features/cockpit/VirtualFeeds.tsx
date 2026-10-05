import { useEffect, useState } from "react";
import { connectQuipStream, type QuipWire } from "../../api";
import { SignalRow, useBoardFilter } from "../../components/BoardFeed";
import { QuipRow } from "../../components/QuipFeed";
import { cn } from "../../lib/utils";
import { useRunStore } from "../../stores/runStoreContext";
import { VirtualFeed } from "./VirtualFeed";

/** The board (every signal on the run) as a windowed list, with group and agent filters. */
export function VirtualBoardFeed() {
  const signals = useRunStore((s) => s.signals);
  const names = useRunStore((s) => s.names);
  const filter = useBoardFilter(signals);
  const { presentGroups, activeGroups, agents, agentFilter, toggleGroup, setAgentFilter, visible, board } = filter;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-2 py-1.5" aria-label="Board filters" role="group">
        {presentGroups.map((label) => {
          const on = activeGroups.has(label);
          return (
            <button
              key={label}
              type="button"
              aria-pressed={on}
              onClick={() => toggleGroup(label)}
              className={cn(
                "rounded-full border px-2 py-0.5 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                on
                  ? "border-primary bg-primary text-primary-foreground hover:border-primary hover:bg-primary/90"
                  : "border-input bg-transparent text-muted-foreground hover:border-input hover:bg-accent",
              )}
            >
              {label}
            </button>
          );
        })}
        {agents.length > 1 && (
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            aria-label="Filter by agent"
            className="ml-auto rounded-md border border-input bg-background px-1.5 py-0.5 text-xs"
          >
            <option value="all">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        )}
      </div>
      <VirtualFeed
        className="min-h-0 flex-1"
        label="Board"
        testId="board-feed"
        items={visible}
        getKey={(s) => s.id}
        estimateSize={72}
        renderRow={(s) => (
          <div className="px-2 py-1">
            <SignalRow signal={s}
              who={s.from_agent && names[s.from_agent] ? `${names[s.from_agent]} (${s.from_agent})` : undefined} />
          </div>
        )}
        empty={board.length === 0 ? "No signals yet. Waiting for the run to start…" : "No signals match the active filters."}
      />
    </div>
  );
}

export interface QuipStream {
  quips: QuipWire[];
  live: boolean;
}

/**
 * The quip log over its own websocket. Quips are a separate log: they are never part of the
 * run-event store and never reach agent context.
 */
export function useQuipStream(runId: string): QuipStream {
  const [quips, setQuips] = useState<QuipWire[]>([]);
  const [live, setLive] = useState(false);
  useEffect(() => {
    setQuips([]);
    const handle = connectQuipStream(
      runId,
      (msg) => {
        if (msg.type === "snapshot" && msg.quips) {
          setQuips(msg.quips);
          setLive(msg.live ?? false);
        } else if (msg.type === "quip" && msg.quip) {
          const quip = msg.quip;
          setQuips((prev) => [...prev, quip]);
        }
      },
      () => setLive(false),
    );
    return () => handle.close();
  }, [runId]);
  return { quips, live };
}

/** The quip feed as a windowed, auto-scrolling list. Pass the stream from `useQuipStream`. */
export function VirtualQuipFeed({ stream }: { stream: QuipStream }) {
  const { quips, live } = stream;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
        <span>In-character commentary on key events.</span>
        <span>{live ? "● live" : "○ from disk"}</span>
      </div>
      <VirtualFeed
        className="min-h-0 flex-1"
        label="Quips"
        testId="quip-feed"
        items={quips}
        getKey={(q, i) => `${q.ts}-${i}`}
        estimateSize={40}
        renderRow={(q) => (
          <div className="px-3 py-1.5">
            <QuipRow quip={q} />
          </div>
        )}
        empty="No quips yet."
      />
    </div>
  );
}
