import { useEffect, useState } from "react";
import { personasApi } from "../api";
import type { DeskFileSummary } from "../api";

interface Props {
  personaId: string;
}

export function DeskFilesSection({ personaId }: Props) {
  const [files, setFiles] = useState<DeskFileSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  // Add form state
  const [addFilename, setAddFilename] = useState("");
  const [addTriggers, setAddTriggers] = useState("");
  const [addContent, setAddContent] = useState("");
  const [addBusy, setBusy] = useState(false);

  // Delete confirm
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, [personaId]);

  async function load() {
    setLoading(true);
    try {
      const res = await personasApi.listDeskFiles(personaId);
      setFiles(res.files ?? []);
    } catch {
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }

  function showToast(msg: string) {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(null), 3000);
  }

  async function handleAdd() {
    if (!addFilename.trim() || !addContent.trim() || addBusy) return;
    setBusy(true);
    const body = {
      filename: addFilename.trim(),
      content: addContent,
      triggers: addTriggers.split(",").map((s) => s.trim()).filter(Boolean),
      agents: [],
      kind: "",
    };
    // Optimistic add
    const optimistic: DeskFileSummary = {
      filename: body.filename,
      triggers: body.triggers,
      agents: [],
      kind: "",
      body_excerpt: body.content.slice(0, 200),
      bytes: body.content.length,
    };
    setFiles((prev) => [optimistic, ...prev]);
    setAddOpen(false);
    setAddFilename("");
    setAddTriggers("");
    setAddContent("");
    try {
      const created = await personasApi.createDeskFile(personaId, body);
      setFiles((prev) => [created, ...prev.filter((f) => f.filename !== optimistic.filename)]);
    } catch (e) {
      setFiles((prev) => prev.filter((f) => f.filename !== optimistic.filename));
      showToast(`Add failed: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(filename: string) {
    setConfirmDelete(null);
    // Optimistic remove
    setFiles((prev) => prev.filter((f) => f.filename !== filename));
    try {
      await personasApi.deleteDeskFile(personaId, filename);
    } catch (e) {
      showToast(`Delete failed: ${String(e)}`);
      void load(); // refetch to restore
    }
  }

  return (
    <div style={{ marginTop: 16 }}>
      {/* Section header */}
      <button
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          fontWeight: 600,
          fontSize: 13,
          color: "#374151",
          width: "100%",
        }}
        onClick={() => setExpanded((v) => !v)}
      >
        <span style={{ fontSize: 10, transition: "transform .15s", transform: expanded ? "rotate(90deg)" : "none" }}>▶</span>
        Desk Files ({loading ? "…" : files.length})
      </button>

      {expanded && (
        <div style={{ marginTop: 8 }}>
          {/* Toast */}
          {toastMsg && (
            <div className="error" style={{ marginBottom: 8, fontSize: 12 }}>{toastMsg}</div>
          )}

          {/* File list */}
          {files.length === 0 && !loading && (
            <p className="muted small">
              No desk files yet. Add one to give this persona pre-work knowledge.
            </p>
          )}

          {files.map((f) => (
            <div key={f.filename} className="card" style={{ marginBottom: 6, padding: "8px 12px" }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <span style={{ fontWeight: 600, fontSize: 12 }}>{f.filename}</span>
                  <div className="row" style={{ gap: 4, marginTop: 2, flexWrap: "wrap" }}>
                    {f.triggers.length === 0 ? (
                      <span className="tag small" title="File won't auto-inject without triggers" style={{ background: "#fef3c7", color: "#92400e" }}>
                        won't auto-inject
                      </span>
                    ) : (
                      f.triggers.map((t) => (
                        <span key={t} className="tag small">{t}</span>
                      ))
                    )}
                  </div>
                </div>
                <button
                  className="small"
                  style={{ color: "#ef4444", background: "none", border: "none", cursor: "pointer" }}
                  onClick={() => setConfirmDelete(f.filename)}
                  title="Delete desk file"
                >
                  ✕
                </button>
              </div>
              {f.body_excerpt && (
                <div className="muted small mono" style={{ marginTop: 4, fontSize: 11, whiteSpace: "pre-wrap" }}>
                  {f.body_excerpt.slice(0, 120)}{f.body_excerpt.length > 120 ? "…" : ""}
                </div>
              )}
            </div>
          ))}

          {/* Delete confirm */}
          {confirmDelete && (
            <div className="card" style={{ padding: 10, marginBottom: 8, border: "1px solid #fca5a5" }}>
              <div className="small">Delete <strong>{confirmDelete}</strong>?</div>
              <div className="row" style={{ gap: 8, marginTop: 6 }}>
                <button className="primary small" style={{ background: "#ef4444" }} onClick={() => void handleDelete(confirmDelete)}>Delete</button>
                <button className="small" onClick={() => setConfirmDelete(null)}>Cancel</button>
              </div>
            </div>
          )}

          {/* Add button / form */}
          {!addOpen && (
            <button
              className="small"
              style={{ marginTop: 4 }}
              onClick={() => setAddOpen(true)}
            >
              + Add desk file
            </button>
          )}

          {addOpen && (
            <div className="card" style={{ marginTop: 8, padding: 10 }}>
              <label className="small">Filename (.md)</label>
              <input
                value={addFilename}
                onChange={(e) => setAddFilename(e.target.value)}
                placeholder="knowledge.md"
                style={{ marginBottom: 6 }}
              />
              <label className="small">Triggers (comma-separated keywords)</label>
              <input
                value={addTriggers}
                onChange={(e) => setAddTriggers(e.target.value)}
                placeholder="schema, api, design"
                style={{ marginBottom: 6 }}
              />
              <label className="small">Content</label>
              <textarea
                rows={4}
                value={addContent}
                onChange={(e) => setAddContent(e.target.value)}
                placeholder="Knowledge content…"
                style={{ marginBottom: 8 }}
              />
              <div className="row" style={{ gap: 8 }}>
                <button
                  className="primary small"
                  onClick={() => void handleAdd()}
                  disabled={addBusy || !addFilename.trim() || !addContent.trim()}
                >
                  {addBusy ? "Adding…" : "Add"}
                </button>
                <button className="small" onClick={() => setAddOpen(false)}>Cancel</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
