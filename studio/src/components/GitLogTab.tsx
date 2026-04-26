import type { Signal } from "../types";

export function GitLogTab({ signals }: { signals: Signal[] }) {
  const commits = signals.filter((s) => s.type === "GIT_COMMIT");
  const merges = signals.filter((s) => s.type === "GIT_MERGE");
  const conflicts = signals.filter((s) => s.type === "GIT_CONFLICT");

  const activity = [...commits, ...merges]
    .sort((a, b) => (a.timestamp ?? "").localeCompare(b.timestamp ?? ""))
    .slice(-8)
    .reverse();

  const hasActivity = activity.length > 0;

  return (
    <div className="card">
      <h2 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Git activity</h2>
      <div className="muted small">
        {commits.length} commit{commits.length !== 1 ? "s" : ""},{" "}
        {merges.length} merge{merges.length !== 1 ? "s" : ""},{" "}
        {conflicts.length} conflict{conflicts.length !== 1 ? "s" : ""}
      </div>
      {!hasActivity ? (
        <p className="muted small" style={{ marginTop: 8 }}>No git activity yet.</p>
      ) : (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
          {activity.map((s) => (
            <div key={s.id} className="small mono" style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
              <span style={{ opacity: 0.5 }}>{s.type === "GIT_MERGE" ? "⇒" : "·"}</span>
              <span>{s.summary}</span>
            </div>
          ))}
        </div>
      )}
      {conflicts.length > 0 && (
        <div className="error" style={{ marginTop: 8 }}>
          {conflicts.length} merge conflict{conflicts.length !== 1 ? "s" : ""} — check the board for resolution.
        </div>
      )}
    </div>
  );
}
