import { useEffect, useRef, useState } from "react";
import { connectQuipStream, type QuipWire, type WsHandle } from "../api";
import { avatarFor } from "../lib/avatar";

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
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Quips</h2>
        <span className="muted small">
          {connected ? "● live" : "○ disk"}
        </span>
      </div>
      <div className="muted small">In-character commentary on key events.</div>
      <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
        {visible.length === 0 && <p className="muted small">No quips yet.</p>}
        {visible.map((q, i) => (
          <QuipRow key={`${q.ts}-${i}`} quip={q} />
        ))}
      </div>
    </div>
  );
}

/** One quip line. Exported for the cockpit's virtualized quip feed. */
export function QuipRow({ quip: q }: { quip: QuipWire }) {
  const av = avatarFor(q.persona || "system");
  return (
    <div className="quip" style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
      <div
        className="worker-avatar"
        style={{ background: av.gradient, width: 24, height: 24, fontSize: 10, flexShrink: 0 }}
        title={q.persona}
      >
        {av.initials}
      </div>
      <div>
        <span className="who">{q.persona}</span>
        <span className="muted small" style={{ margin: "0 4px" }}>
          {EVENT_LABEL[q.event] ?? q.event}
        </span>
        <em>{q.text}</em>
      </div>
    </div>
  );
}
