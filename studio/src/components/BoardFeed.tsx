import { useMemo, useState } from "react";
import type { Signal } from "../types";

// Signal types grouped into logical buckets for the filter bar.
const TYPE_GROUPS: { label: string; types: string[]; color: string }[] = [
  { label: "Status",   types: ["STARTED", "COMPLETE", "FAILED", "REJECTED", "REJECTED_RETRYING"], color: "#4f46e5" },
  { label: "Protocol", types: ["PROTOCOL", "COMMIT", "VERIFY_RESULT", "ASK", "ANSWER", "SKIP_REQUEST", "SKIP_GRANTED", "SKIP_DENIED"], color: "#0891b2" },
  { label: "Tasks",    types: ["TASK_ASSIGNED", "TASK_COMPLETE", "TASK_CLOSED", "STORY_OPENED", "STORY_CLOSED"], color: "#059669" },
  { label: "Git",      types: ["GIT_COMMIT", "GIT_MERGE"], color: "#d97706" },
  { label: "User",     types: ["USER_COMMENT", "APPROVAL_NEEDED", "APPROVAL_GRANTED", "APPROVAL_DENIED", "HUMAN_NEEDED", "HUMAN_RESPONDED"], color: "#db2777" },
];

const ALL_GROUP_LABELS = TYPE_GROUPS.map((g) => g.label);

function typeToGroup(type: string): string | null {
  for (const g of TYPE_GROUPS) {
    if (g.types.includes(type)) return g.label;
  }
  return null;
}

export function BoardFeed({ signals }: { signals: Signal[] }) {
  const board = signals.filter((s) => !isQuip(s));

  // Which groups are present in the current signal set
  const presentGroups = useMemo(() => {
    const seen = new Set<string>();
    for (const s of board) {
      const g = typeToGroup(s.type);
      if (g) seen.add(g);
      else seen.add("Other");
    }
    return ALL_GROUP_LABELS.filter((g) => seen.has(g));
  }, [board]);

  const [activeGroups, setActiveGroups] = useState<Set<string>>(
    () => new Set(ALL_GROUP_LABELS),
  );

  const [agentFilter, setAgentFilter] = useState<string>("all");

  // Unique agents for the agent filter
  const agents = useMemo(() => {
    const seen = new Set<string>();
    for (const s of board) if (s.from_agent) seen.add(s.from_agent);
    return [...seen].sort();
  }, [board]);

  function toggleGroup(label: string) {
    setActiveGroups((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }

  const visible = board.filter((s) => {
    const g = typeToGroup(s.type) ?? "Other";
    if (!activeGroups.has(g) && !activeGroups.has("Other")) return false;
    if (g === "Other" && !activeGroups.has("Other")) return false;
    if (!activeGroups.has(g)) return false;
    if (agentFilter !== "all" && s.from_agent !== agentFilter && !s.from_user) return false;
    return true;
  });

  return (
    <div>
      {/* Filter bar */}
      <div style={{ padding: "8px 12px", borderBottom: "1px solid #e2e8f0", display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
        {presentGroups.map((label) => {
          const group = TYPE_GROUPS.find((g) => g.label === label);
          const active = activeGroups.has(label);
          return (
            <button
              key={label}
              onClick={() => toggleGroup(label)}
              style={{
                padding: "2px 8px",
                borderRadius: 12,
                border: `1px solid ${group?.color ?? "#94a3b8"}`,
                background: active ? (group?.color ?? "#94a3b8") : "transparent",
                color: active ? "#fff" : (group?.color ?? "#64748b"),
                fontSize: 11,
                fontWeight: 600,
                cursor: "pointer",
                transition: "all .1s",
              }}
            >
              {label}
            </button>
          );
        })}
        {agents.length > 1 && (
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            style={{ marginLeft: "auto", fontSize: 11, padding: "2px 6px", borderRadius: 6 }}
          >
            <option value="all">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        )}
      </div>

      <div className="feed">
        {visible.length === 0 && (
          <p className="muted small">
            {board.length === 0
              ? "No signals yet — waiting for the run to start…"
              : "No signals match the active filters."}
          </p>
        )}
        {visible.map((s) => (
          <SignalRow key={s.id} signal={s} />
        ))}
      </div>
    </div>
  );
}

function SignalRow({ signal }: { signal: Signal }) {
  const time = signal.timestamp?.replace("T", " ").split(".")[0]?.slice(11) ?? "";
  const kind = classify(signal);
  return (
    <div className={`signal ${kind}`}>
      <div className="sig-head">
        <span className="mono">{time}</span>
        <span className={`tag tag-status-${statusClass(signal.type)}`}>
          {signal.type}
        </span>
        {signal.from_agent && (
          <span className="muted small">{signal.from_agent}</span>
        )}
        {signal.iteration > 0 && (
          <span className="muted small">iter {signal.iteration}</span>
        )}
      </div>
      <div className="sig-summary">{signal.summary}</div>
      {signal.detail && (
        <pre>{signal.detail}</pre>
      )}
    </div>
  );
}

function classify(s: Signal): string {
  if (s.from_user) return "user";
  if (s.type === "PROTOCOL") return "protocol";
  return "";
}

function statusClass(type: string): string {
  if (type === "COMPLETE" || type === "APPROVED") return "complete";
  if (type === "FAILED" || type === "REJECTED") return "failed";
  if (type === "STARTED" || type === "THINKING") return "running";
  return "queued";
}

function isQuip(_s: Signal): boolean {
  return false;
}
