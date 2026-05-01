import { useEffect, useState } from "react";
import { getToken } from "../api";

interface DirEntry {
  name: string;
  path: string;
}

interface ListResponse {
  path: string;
  parent: string | null;
  dirs: DirEntry[];
}

interface Props {
  initialPath?: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}

/** Modal folder browser hitting GET /api/fs/list. The user navigates the
 *  API host's filesystem and clicks Select on a directory to commit. */
export function FolderPicker({ initialPath, onSelect, onClose }: Props) {
  const [data, setData] = useState<ListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load(path: string) {
    setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const res = await fetch(
        `/api/fs/list?path=${encodeURIComponent(path)}`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} },
      );
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `${res.status}`);
      }
      setData((await res.json()) as ListResponse);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(initialPath || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ minWidth: 480, maxWidth: 640 }}
      >
        <h3 style={{ marginTop: 0 }}>Select folder</h3>

        <div className="row" style={{ gap: 6, alignItems: "center", marginBottom: 8 }}>
          <button
            disabled={!data?.parent || loading}
            onClick={() => data?.parent && void load(data.parent)}
            title="Go up one level"
          >
            ↑ Up
          </button>
          <input
            type="text"
            value={data?.path ?? ""}
            placeholder="(roots)"
            readOnly
            className="mono small"
            style={{ flex: 1 }}
          />
        </div>

        {error && <div className="error" style={{ marginBottom: 8 }}>{error}</div>}

        <div
          style={{
            maxHeight: 320,
            overflowY: "auto",
            border: "1px solid #e2e8f0",
            borderRadius: 6,
          }}
        >
          {loading ? (
            <p className="muted small" style={{ padding: 12 }}>Loading…</p>
          ) : data && data.dirs.length === 0 ? (
            <p className="muted small" style={{ padding: 12 }}>
              No subdirectories here.
            </p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {data?.dirs.map((d) => (
                <li
                  key={d.path}
                  onClick={() => void load(d.path)}
                  style={{
                    padding: "6px 12px",
                    cursor: "pointer",
                    borderBottom: "1px solid #f1f5f9",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "#f8fafc")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  📁 {d.name}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="row" style={{ marginTop: 12, gap: 8, justifyContent: "flex-end" }}>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!data?.path}
            onClick={() => {
              if (data?.path) {
                onSelect(data.path);
                onClose();
              }
            }}
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  );
}
