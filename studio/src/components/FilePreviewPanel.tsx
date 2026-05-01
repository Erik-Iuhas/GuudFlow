import { useEffect, useState } from "react";
import { useFileContent, useFileHistory } from "../hooks/useFileContent";

interface Props {
  runId: string;
  filePath: string | null;
  onClose: () => void;
}

export function FilePreviewPanel({ runId, filePath, onClose }: Props) {
  const [tab, setTab] = useState<"current" | "history">("current");
  const [selectedRef, setSelectedRef] = useState<string | null>(null);

  // Reset tab/ref when file changes
  useEffect(() => {
    setTab("current");
    setSelectedRef(null);
  }, [filePath]);

  // Close on Escape
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const { data, loading, error } = useFileContent(runId, filePath, selectedRef);
  const { history, loading: histLoading } = useFileHistory(runId, tab === "history" ? filePath : null);

  if (!filePath) return null;

  const filename = filePath.split("/").pop() ?? filePath;

  return (
    <>
      <div className="slide-over-backdrop" onClick={onClose} />
      <div className="slide-over" role="dialog" aria-modal style={{ width: 560 }}>
        <div className="slide-over-header">
          <div>
            <span style={{ fontWeight: 600 }}>{filename}</span>
            <span className="mono muted small" style={{ marginLeft: 8 }}>{filePath}</span>
          </div>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        <div className="tabs" style={{ padding: "0 16px", borderBottom: "1px solid #e2e8f0" }}>
          <button
            className={`tab ${tab === "current" ? "tab-active" : ""}`}
            onClick={() => setTab("current")}
          >
            Current
          </button>
          <button
            className={`tab ${tab === "history" ? "tab-active" : ""}`}
            onClick={() => setTab("history")}
          >
            History
          </button>
        </div>

        <div className="slide-over-body" style={{ padding: 0 }}>
          {tab === "current" && (
            <>
              {selectedRef && (
                <div
                  className="small muted"
                  style={{
                    padding: "6px 16px",
                    background: "#fef3c7",
                    borderBottom: "1px solid #fde68a",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  Viewing at <code>{selectedRef.slice(0, 7)}</code>
                  <button
                    className="small"
                    onClick={() => setSelectedRef(null)}
                    style={{ fontSize: 11 }}
                  >
                    Back to current
                  </button>
                </div>
              )}
              {loading && <div className="muted small" style={{ padding: 16 }}>Loading…</div>}
              {error && <div className="error" style={{ padding: 16 }}>{error}</div>}
              {data?.error && (
                <div className="muted small" style={{ padding: 16 }}>
                  {data.error === "binary or too large"
                    ? "This file is binary or too large to preview."
                    : data.error}
                </div>
              )}
              {data?.content !== undefined && (
                <pre
                  className="mono small"
                  style={{
                    margin: 0,
                    padding: 16,
                    overflowX: "auto",
                    whiteSpace: "pre",
                    fontSize: 12,
                    lineHeight: 1.6,
                    background: "#0f172a",
                    color: "#e2e8f0",
                  }}
                >
                  {data.content}
                </pre>
              )}
            </>
          )}

          {tab === "history" && (
            <div style={{ padding: 12 }}>
              {histLoading && <div className="muted small">Loading history…</div>}
              {!histLoading && history.length === 0 && (
                <div className="muted small">No commits touching this file.</div>
              )}
              {history.map((entry) => (
                <div
                  key={entry.sha}
                  className="card"
                  style={{
                    marginBottom: 8,
                    padding: "8px 12px",
                    cursor: "pointer",
                    border: selectedRef === entry.sha ? "1px solid #6366f1" : undefined,
                  }}
                  onClick={() => {
                    setSelectedRef(entry.sha);
                    setTab("current");
                  }}
                >
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="small" style={{ fontWeight: 600 }}>{entry.subject || "(no message)"}</span>
                    <span className="mono small muted">{entry.sha.slice(0, 7)}</span>
                  </div>
                  <div className="small muted">
                    {entry.author_role} · {new Date(entry.committed_at * 1000).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
