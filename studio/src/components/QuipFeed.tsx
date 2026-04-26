import type { Signal } from "../types";

export function QuipFeed({ signals }: { signals: Signal[] }) {
  // Quips currently land in a separate file (data/runs/{id}/quip_log.json)
  // not the board. For Stage 8 MVP we synthesise pseudo-quips from board
  // events so the pane is never empty in dev. Real quip stream wires
  // into a parallel WebSocket later.
  const synthQuips = signals
    .filter((s) => s.type === "APPROVED" || s.type === "REJECTED" || s.type === "COMPLETE" || s.type === "FAILED")
    .slice(-5);

  return (
    <div className="card">
      <h2 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Quips</h2>
      <div className="muted small">In-character commentary on key events.</div>
      <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
        {synthQuips.length === 0 && <p className="muted small">No quips yet.</p>}
        {synthQuips.map((s) => (
          <div className="quip" key={s.id}>
            <span className="who">{s.from_agent ?? "system"}:</span>
            <em>{quipFor(s)}</em>
          </div>
        ))}
      </div>
    </div>
  );
}

function quipFor(s: { type: string; summary: string; from_agent?: string | null }): string {
  switch (s.type) {
    case "APPROVED":
      return "ship it.";
    case "REJECTED":
      return "needs another pass — see notes on the board.";
    case "COMPLETE":
      return "done with this step.";
    case "FAILED":
      return "ran into a wall here.";
    default:
      return s.summary;
  }
}
