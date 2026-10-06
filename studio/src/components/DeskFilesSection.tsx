import { useEffect, useState } from "react";
import { personasApi } from "../api";
import type { DeskFileSummary } from "../api";
import { errorCls, fieldCls, labelCls } from "../lib/field";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

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
    <div className="mt-4">
      {/* Section header */}
      <button
        type="button"
        className="flex w-full cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-[13px] font-semibold text-foreground"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className={cn("text-[10px] transition-transform", expanded && "rotate-90")}>▶</span>
        Desk Files ({loading ? "…" : files.length})
      </button>

      {expanded && (
        <div className="mt-2">
          {/* Toast */}
          {toastMsg && (
            <div className={cn(errorCls, "mb-2 text-xs")}>{toastMsg}</div>
          )}

          {/* File list */}
          {files.length === 0 && !loading && (
            <p className="my-2 text-xs text-muted-foreground">
              No desk files yet. Add one to give this persona pre-work knowledge.
            </p>
          )}

          {files.map((f) => (
            <Card key={f.filename} className="mb-1.5 px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <span className="text-xs font-semibold">{f.filename}</span>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1">
                    {f.triggers.length === 0 ? (
                      <Badge variant="blocked" title="File won't auto-inject without triggers">
                        won't auto-inject
                      </Badge>
                    ) : (
                      f.triggers.map((t) => (
                        <Badge key={t} variant="secondary">{t}</Badge>
                      ))
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  className="cursor-pointer border-0 bg-transparent p-1 text-xs text-destructive"
                  onClick={() => setConfirmDelete(f.filename)}
                  title="Delete desk file"
                >
                  ✕
                </button>
              </div>
              {f.body_excerpt && (
                <div className="mt-1 whitespace-pre-wrap font-mono text-[11px] text-muted-foreground">
                  {f.body_excerpt.slice(0, 120)}{f.body_excerpt.length > 120 ? "…" : ""}
                </div>
              )}
            </Card>
          ))}

          {/* Delete confirm */}
          {confirmDelete && (
            <Card className="mb-2 border-destructive/40 p-2.5">
              <div className="text-xs">Delete <strong>{confirmDelete}</strong>?</div>
              <div className="mt-1.5 flex items-center gap-2">
                <Button size="sm" variant="destructive" onClick={() => void handleDelete(confirmDelete)}>Delete</Button>
                <Button size="sm" variant="outline" onClick={() => setConfirmDelete(null)}>Cancel</Button>
              </div>
            </Card>
          )}

          {/* Add button / form */}
          {!addOpen && (
            <Button size="sm" variant="outline" className="mt-1" onClick={() => setAddOpen(true)}>
              + Add desk file
            </Button>
          )}

          {addOpen && (
            <Card className="mt-2 p-2.5">
              <label className={labelCls}>Filename (.md)</label>
              <input
                className={cn(fieldCls, "mb-1.5")}
                value={addFilename}
                onChange={(e) => setAddFilename(e.target.value)}
                placeholder="knowledge.md"
              />
              <label className={labelCls}>Triggers (comma-separated keywords)</label>
              <input
                className={cn(fieldCls, "mb-1.5")}
                value={addTriggers}
                onChange={(e) => setAddTriggers(e.target.value)}
                placeholder="schema, api, design"
              />
              <label className={labelCls}>Content</label>
              <textarea
                className={cn(fieldCls, "mb-2")}
                rows={4}
                value={addContent}
                onChange={(e) => setAddContent(e.target.value)}
                placeholder="Knowledge content…"
              />
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  onClick={() => void handleAdd()}
                  disabled={addBusy || !addFilename.trim() || !addContent.trim()}
                >
                  {addBusy ? "Adding…" : "Add"}
                </Button>
                <Button size="sm" variant="outline" onClick={() => setAddOpen(false)}>Cancel</Button>
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
