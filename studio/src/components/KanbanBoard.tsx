import { useState } from "react";
import { runsApi } from "../api";
import { avatarFor } from "../lib/avatar";
import { useDesks } from "../hooks/useDesks";
import type { Task } from "../types";

const COLUMNS: { id: string; label: string; color: string }[] = [
  { id: "PENDING",     label: "Backlog",     color: "#f1f5f9" },
  { id: "IN_PROGRESS", label: "In Progress", color: "#fef3c7" },
  { id: "COMPLETE",    label: "Done",        color: "#d1fae5" },
];

interface Props {
  runId: string;
  agents: { role: string; persona_id?: string | null }[];
  onCardClick?: (role: string) => void;
}

interface TaskCard {
  task: Task;
  role: string;
  personaId?: string | null;
}

export function KanbanBoard({ runId, agents, onCardClick }: Props) {
  const { data: desksData } = useDesks(runId);
  const [note, setNote] = useState("");
  const [posting, setPosting] = useState(false);

  async function postNote() {
    if (!note.trim() || posting) return;
    setPosting(true);
    try {
      await runsApi.postComment(runId, note.trim(), "normal");
      setNote("");
    } catch {
      // silent — comment failed
    } finally {
      setPosting(false);
    }
  }

  // Build per-column task lists from desks data
  const byColumn = new Map<string, TaskCard[]>();
  for (const col of COLUMNS) byColumn.set(col.id, []);

  if (desksData) {
    for (const agent of agents) {
      const tasks: Task[] = desksData.desks[agent.role] ?? [];
      for (const task of tasks) {
        const status = task.status ?? "PENDING";
        const colId = COLUMNS.find((c) => c.id === status) ? status : "PENDING";
        byColumn.get(colId)?.push({ task, role: agent.role, personaId: agent.persona_id });
      }
    }
  }

  return (
    <div
      className="kanban-board"
      style={{ display: "flex", flexDirection: "column", gap: 12, height: "100%" }}
    >
      <div style={{ display: "flex", gap: 12, overflowX: "auto", flex: 1, alignItems: "flex-start" }}>
        {COLUMNS.map((col) => {
          const cards = byColumn.get(col.id) ?? [];
          return (
            <div
              key={col.id}
              className="kanban-column"
              style={{
                minWidth: 200,
                flex: "1 1 0",
                background: col.color,
                borderRadius: 8,
                padding: 10,
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 8, color: "#374151" }}>
                {col.label}
                <span className="muted small" style={{ marginLeft: 6 }}>({cards.length})</span>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {cards.map(({ task, role, personaId }) => {
                  const av = avatarFor(personaId || role);
                  return (
                    <div
                      key={task.task_id}
                      className="kanban-card"
                      style={{
                        background: "#fff",
                        borderRadius: 6,
                        padding: "8px 10px",
                        cursor: onCardClick ? "pointer" : "default",
                        boxShadow: "0 1px 3px rgba(0,0,0,.1)",
                      }}
                      onClick={() => onCardClick?.(role)}
                    >
                      <div className="row" style={{ gap: 6, alignItems: "center", marginBottom: 4 }}>
                        <div
                          className="worker-avatar"
                          style={{ background: av.gradient, width: 22, height: 22, fontSize: 9, flexShrink: 0 }}
                        >
                          {av.initials}
                        </div>
                        <span style={{ fontSize: 11, color: "#6b7280", fontWeight: 500 }}>{role}</span>
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          fontWeight: 500,
                          overflow: "hidden",
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                        }}
                        title={task.goal}
                      >
                        {task.goal}
                      </div>
                    </div>
                  );
                })}

                {cards.length === 0 && (
                  <div className="muted small" style={{ fontSize: 11, padding: "4px 0" }}>
                    No tasks
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Comment box at bottom of Kanban */}
      <div
        style={{
          borderTop: "1px solid #e5e7eb",
          paddingTop: 10,
          display: "flex",
          gap: 8,
          alignItems: "flex-end",
        }}
      >
        <textarea
          rows={2}
          placeholder="Post a comment for the team…"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void postNote(); }}
          style={{ flex: 1, fontSize: 12, resize: "none" }}
        />
        <button
          className="primary small"
          onClick={() => void postNote()}
          disabled={posting || !note.trim()}
          style={{ fontSize: 11, whiteSpace: "nowrap" }}
        >
          {posting ? "Posting…" : "Post"}
        </button>
      </div>
    </div>
  );
}
