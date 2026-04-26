import type { Signal } from "../types";

export function GitLogTab({ signals }: { signals: Signal[] }) {
  const commits = signals.filter((s) => s.type === "GIT_COMMIT");
  const merges = signals.filter((s) => s.type === "GIT_MERGE");
  const conflicts = signals.filter((s) => s.type === "GIT_CONFLICT");

  return (
    <div className="card">
      <h2 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Git activity</h2>
      <div className="muted small">
        {commits.length} commits, {merges.length} merges, {conflicts.length} conflicts
      </div>
      {commits.length === 0 ? (
        <p className="muted small" style={{ marginTop: 8 }}>No commits yet.</p>
      ) : (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
          {commits.slice(-8).reverse().map((c) => (
            <div key={c.id} className="small mono">
              {c.summary}
            </div>
          ))}
        </div>
      )}
      {conflicts.length > 0 && (
        <div className="error" style={{ marginTop: 8 }}>
          {conflicts.length} merge conflict(s) — check the board for resolution.
        </div>
      )}
    </div>
  );
}
