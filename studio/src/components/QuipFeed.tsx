import { useEffect, useRef, useState } from "react";
import { connectQuipStream, type QuipWire } from "../api";

interface Props {
  runId: string;
}

export function QuipFeed({ runId }: Props) {
  const [quips, setQuips] = useState<QuipWire[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

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
          <div className="quip" key={`${q.ts}-${i}`}>
            <span className="who">{q.persona}:</span>
            <em>{q.text}</em>
            <span className="muted small" style={{ marginLeft: 8 }}>
              ({q.event})
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
