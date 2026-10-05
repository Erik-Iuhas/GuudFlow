import { useEffect, useRef, useState } from "react";
import { connectQuipStream, type QuipWire, type WsHandle } from "../api";
import { AvatarDot } from "./AvatarDot";
import { Card } from "./ui/card";

const EVENT_LABEL: Record<string, string> = {
  agent_complete: "·",
  approved: "✓",
  rejected: "✗",
  run_complete: "·",
  iteration_complete: "·",
};

interface Props {
  runId: string;
}

export function QuipFeed({ runId }: Props) {
  const [quips, setQuips] = useState<QuipWire[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WsHandle | null>(null);

  useEffect(() => {
    const ws = connectQuipStream(
      runId,
      (msg) => {
        if (msg.type === "snapshot" && msg.quips) {
          setQuips(msg.quips);
          setConnected(msg.live ?? false);
        } else if (msg.type === "quip" && msg.quip) {
          setQuips((prev) => [...prev, msg.quip as QuipWire]);
        }
        // type === "error" is silently absorbed — the pane just stays empty.
      },
      () => setConnected(false),
    );
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [runId]);

  // Show the most recent N so the pane stays compact.
  const visible = quips.slice(-8);

  return (
    <Card className="mb-3 p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="m-0 text-[13px] font-semibold">Quips</h2>
        <span className="text-xs text-muted-foreground">
          {connected ? "● live" : "○ disk"}
        </span>
      </div>
      <div className="text-xs text-muted-foreground">In-character commentary on key events.</div>
      <div className="mt-2 flex flex-col gap-1.5">
        {visible.length === 0 && <p className="m-0 text-xs text-muted-foreground">No quips yet.</p>}
        {visible.map((q, i) => (
          <QuipRow key={`${q.ts}-${i}`} quip={q} />
        ))}
      </div>
    </Card>
  );
}

/** One quip line. Exported for the cockpit's virtualized quip feed. */
export function QuipRow({ quip: q }: { quip: QuipWire }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-status-blocked/30 bg-status-blocked/10 px-3 py-2 text-xs">
      <AvatarDot seed={q.persona || "system"} title={q.persona} className="h-6 w-6 text-[10px]" />
      <div>
        <span className="mr-1.5 font-semibold text-status-blocked">{q.persona}</span>
        <span className="mx-1 text-xs text-muted-foreground">{EVENT_LABEL[q.event] ?? q.event}</span>
        <em>{q.text}</em>
      </div>
    </div>
  );
}
