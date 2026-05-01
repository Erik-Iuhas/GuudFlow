import { useEffect, useState } from "react";
import { getToken } from "../api";

interface Props {
  runId: string;
  sha: string | null;
  onClose: () => void;
}

interface DiffData {
  sha: string;
  diff: string;
}

export function GitDiffSideOver({ runId, sha, onClose }: Props) {
  const [data, setData] = useState<DiffData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sha) return;
    setLoading(true);
    setError(null);
    setData(null);
    const token = getToken();
    fetch(
      `/api/runs/${encodeURIComponent(runId)}/git/diff?ref=${encodeURIComponent(sha)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    )
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<DiffData>;
      })
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [runId, sha]);

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!sha) return null;

  return (
    <>
      <div className="slide-over-backdrop" onClick={onClose} />
      <div className="slide-over" role="dialog" aria-modal style={{ width: 600 }}>
        <div className="slide-over-header">
          <div>
            <span style={{ fontWeight: 600 }}>Commit diff</span>
            <span className="mono small muted" style={{ marginLeft: 8 }}>{sha.slice(0, 7)}</span>
          </div>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>
        <div className="slide-over-body" style={{ padding: 0 }}>
          {loading && <div className="muted small" style={{ padding: 16 }}>Loading diff…</div>}
          {error && <div className="error" style={{ padding: 16 }}>{error}</div>}
          {data && (
            <pre
              className="mono small"
              style={{
                margin: 0,
                padding: 16,
                overflowX: "auto",
                whiteSpace: "pre",
                fontSize: 12,
                lineHeight: 1.5,
                background: "#0f172a",
                color: "#e2e8f0",
              }}
            >
              {renderDiff(data.diff)}
            </pre>
          )}
        </div>
      </div>
    </>
  );
}

function renderDiff(raw: string): React.ReactNode {
  if (!raw.trim()) return <span className="muted">Empty diff.</span>;
  return raw.split("\n").map((line, i) => {
    let color = "#e2e8f0";
    if (line.startsWith("+") && !line.startsWith("+++")) color = "#4ade80";
    else if (line.startsWith("-") && !line.startsWith("---")) color = "#f87171";
    else if (line.startsWith("@@")) color = "#7dd3fc";
    else if (line.startsWith("diff ") || line.startsWith("index ")) color = "#94a3b8";
    return (
      <span key={i} style={{ color, display: "block" }}>
        {line || " "}
      </span>
    );
  });
}
