import type { Signal } from "../types";

interface FileEntry {
  path: string;
  status: "claimed" | "written" | "deleted";
  agent: string;
  claimedAt?: string;
}

function buildFileTree(signals: Signal[]): Map<string, FileEntry> {
  const files = new Map<string, FileEntry>();
  for (const sig of signals) {
    if (sig.type === "FILE_CLAIM") {
      let detail: Record<string, string> = {};
      try { detail = JSON.parse(sig.detail || "{}"); } catch { /* ignore */ }
      const path = detail.path || sig.summary.replace(/^[^:]+: claim: /, "");
      files.set(path, {
        path,
        status: "claimed",
        agent: sig.from_agent || "unknown",
        claimedAt: sig.timestamp,
      });
    } else if (sig.type === "FILE_RELEASE") {
      let detail: Record<string, string> = {};
      try { detail = JSON.parse(sig.detail || "{}"); } catch { /* ignore */ }
      const path = detail.path || sig.summary.replace(/^[^:]+: release: /, "");
      const outcome = detail.outcome || "written";
      const existing = files.get(path);
      files.set(path, {
        path,
        status: outcome === "deleted" ? "deleted" : "written",
        agent: existing?.agent || sig.from_agent || "unknown",
      });
    }
  }
  return files;
}

function groupByDir(files: Map<string, FileEntry>): Map<string, FileEntry[]> {
  const dirs = new Map<string, FileEntry[]>();
  for (const entry of files.values()) {
    const parts = entry.path.split("/");
    const dir = parts.length > 1 ? parts.slice(0, -1).join("/") : "(root)";
    if (!dirs.has(dir)) dirs.set(dir, []);
    dirs.get(dir)!.push(entry);
  }
  // Sort each directory's files alphabetically
  for (const entries of dirs.values()) {
    entries.sort((a, b) => a.path.localeCompare(b.path));
  }
  return new Map([...dirs.entries()].sort(([a], [b]) => a.localeCompare(b)));
}

const AGENT_COLORS: Record<string, string> = {
  game_designer: "#7c3aed",
  pygame_backend_dev: "#0369a1",
  pygame_frontend_dev: "#b45309",
  meta_agent: "#374151",
};

function agentColor(role: string): string {
  return AGENT_COLORS[role] ?? "#475569";
}

interface Props {
  signals: Signal[];
}

export function FileTreeTab({ signals }: Props) {
  const files = buildFileTree(signals);
  const grouped = groupByDir(files);

  if (files.size === 0) {
    return <p className="muted small" style={{ padding: 12 }}>No files written yet.</p>;
  }

  const activeClaims = [...files.values()].filter((f) => f.status === "claimed");
  const writtenCount = [...files.values()].filter((f) => f.status === "written").length;

  return (
    <div style={{ fontFamily: "monospace", fontSize: 13 }}>
      {/* Summary bar */}
      <div
        className="row"
        style={{
          padding: "6px 12px",
          background: "#f8fafc",
          borderBottom: "1px solid #e2e8f0",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <span className="small muted">{files.size} files tracked</span>
        <span className="small" style={{ color: "#16a34a" }}>
          ✓ {writtenCount} written
        </span>
        {activeClaims.length > 0 && (
          <span className="small" style={{ color: "#b45309" }}>
            ✎ {activeClaims.length} in progress
          </span>
        )}
      </div>

      {/* Active claims banner */}
      {activeClaims.length > 0 && (
        <div style={{ padding: "8px 12px", borderBottom: "1px solid #fde68a", background: "#fefce8" }}>
          {activeClaims.map((f) => (
            <div key={f.path} className="row" style={{ gap: 8, marginBottom: 2 }}>
              <span
                style={{
                  display: "inline-block",
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: agentColor(f.agent),
                  animation: "pulse 1.5s infinite",
                  flexShrink: 0,
                  marginTop: 4,
                }}
              />
              <span style={{ color: agentColor(f.agent), fontWeight: 600 }}>{f.agent}</span>
              <span style={{ color: "#374151" }}>is writing</span>
              <span style={{ color: "#1e40af" }}>{f.path}</span>
            </div>
          ))}
        </div>
      )}

      {/* Directory tree */}
      <div style={{ padding: "8px 0" }}>
        {[...grouped.entries()].map(([dir, entries]) => (
          <div key={dir} style={{ marginBottom: 4 }}>
            <div
              style={{
                padding: "2px 12px",
                color: "#64748b",
                fontWeight: 700,
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              {dir}/
            </div>
            {entries.map((f) => {
              const filename = f.path.split("/").pop()!;
              const isClaimed = f.status === "claimed";
              const isDeleted = f.status === "deleted";
              return (
                <div
                  key={f.path}
                  className="row"
                  style={{
                    padding: "3px 12px 3px 24px",
                    gap: 8,
                    background: isClaimed ? "#fefce8" : "transparent",
                    opacity: isDeleted ? 0.4 : 1,
                  }}
                >
                  <span style={{ color: isClaimed ? "#d97706" : isDeleted ? "#ef4444" : "#16a34a", flexShrink: 0 }}>
                    {isClaimed ? "✎" : isDeleted ? "✗" : "✓"}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      textDecoration: isDeleted ? "line-through" : "none",
                      color: isClaimed ? "#92400e" : "#1e293b",
                      fontWeight: isClaimed ? 600 : 400,
                    }}
                  >
                    {filename}
                  </span>
                  <span
                    className="small"
                    style={{
                      color: agentColor(f.agent),
                      background: `${agentColor(f.agent)}18`,
                      padding: "1px 6px",
                      borderRadius: 4,
                      flexShrink: 0,
                    }}
                  >
                    {f.agent}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
