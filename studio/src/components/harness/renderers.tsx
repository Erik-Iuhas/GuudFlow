// Minimal renderers for the signals the opencode harness posts. All content is
// rendered as React text nodes (never as HTML).

import { useState, type ReactNode } from "react";
import type { Signal } from "../../types";
import {
  CAPABILITY_ORDER,
  formatDuration,
  parseAgentResult,
  parseCapabilities,
  parseToolCall,
  parseTodos,
  type AgentResultInfo,
  type CapabilitiesInfo,
  type TodoItem,
  type ToolCallInfo,
} from "./parse";

const LONG_TEXT_CHARS = 240;

const okColor = "#059669";
const errColor = "#dc2626";
const warnColor = "#b45309";

function Badge({ color, children, title }: { color: string; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      style={{
        padding: "1px 7px", borderRadius: 10, fontSize: 11, fontWeight: 600,
        color: "#fff", background: color,
      }}
    >
      {children}
    </span>
  );
}

/** Long text collapsed behind a <details>; short text shown directly. */
export function CollapsibleText({ text, label = "Show full text" }: { text: string; label?: string }) {
  if (text.length <= LONG_TEXT_CHARS) return <pre>{text}</pre>;
  return (
    <details className="harness-collapsible">
      <summary className="small muted" style={{ cursor: "pointer" }}>
        {label} ({text.length} chars): {text.slice(0, 80).replace(/\s+/g, " ")}…
      </summary>
      <pre>{text}</pre>
    </details>
  );
}

export function ToolCallRow({ info, summary }: { info: ToolCallInfo; summary?: string }) {
  const [open, setOpen] = useState(false);
  const body = info.ok ? info.output : info.error;
  return (
    <div className="harness-tool-call">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex", gap: 8, alignItems: "center", width: "100%", textAlign: "left",
          background: "none", border: "none", padding: "2px 0", cursor: "pointer", font: "inherit",
        }}
        title={summary}
      >
        <span className="small muted">{open ? "▾" : "▸"}</span>
        <strong className="mono">{info.tool}</strong>
        {info.target && (
          <span className="mono small muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 360 }}>
            {info.target}
          </span>
        )}
        <Badge color={info.ok ? okColor : errColor}>{info.ok ? "ok" : "error"}</Badge>
        {info.durationMs !== null && <span className="small muted">{formatDuration(info.durationMs)}</span>}
      </button>
      {open && (
        <div style={{ marginLeft: 16 }}>
          <div className="small muted">Input</div>
          <pre>{info.input || "(none)"}</pre>
          <div className="small muted">{info.ok ? "Output" : "Error"}</div>
          <pre>{body || "(none)"}</pre>
        </div>
      )}
    </div>
  );
}

function fmtCost(c: number): string {
  return `$${c.toFixed(c < 0.1 ? 4 : 2)}`;
}

export function AgentResultCard({ info }: { info: AgentResultInfo }) {
  const stats: string[] = [];
  if (info.cost !== null) stats.push(fmtCost(info.cost));
  if (info.tokens !== null) stats.push(`${info.tokens.toLocaleString("en-US")} tokens`);
  if (info.toolCalls !== null) stats.push(`${info.toolCalls} tool calls`);
  if (info.commits !== null) stats.push(`${info.commits} commits`);

  const reviewer = info.kind === "reviewer";
  const label = reviewer ? (info.verdict ?? "") : (info.status ?? "");
  const good = reviewer ? info.verdict === "pass" : info.status === "done";
  const bad = reviewer ? info.verdict === "fail" : info.status === "failed";
  const color = good ? okColor : bad ? errColor : warnColor;

  return (
    <div className="harness-agent-result">
      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        {label && <Badge color={color}>{label}</Badge>}
        {stats.length > 0 && <span className="small muted">{stats.join(" · ")}</span>}
      </div>
      {info.summary && <p style={{ margin: "4px 0" }}>{info.summary}</p>}
      {reviewer && info.issues.length > 0 && (
        <ul aria-label="Review issues" style={{ margin: "4px 0", paddingLeft: 18 }}>
          {info.issues.map((i, n) => (
            <li key={n}>
              <span className="mono small">{i.file}</span>: {i.detail}
            </li>
          ))}
        </ul>
      )}
      {info.files.length > 0 && (
        <div className="small">
          <span className="muted">Files changed ({info.files.length}): </span>
          <span className="mono">{info.files.join(", ")}</span>
        </div>
      )}
    </div>
  );
}

const TODO_MARK: Record<string, string> = {
  completed: "☑",
  in_progress: "▶",
  cancelled: "☒",
  pending: "☐",
};

export function TodoList({ items }: { items: TodoItem[] }) {
  return (
    <ul aria-label="Agent todos" style={{ listStyle: "none", margin: "4px 0", padding: 0 }}>
      {items.map((t, i) => (
        <li
          key={i}
          data-status={t.status}
          style={{
            textDecoration: t.status === "completed" || t.status === "cancelled" ? "line-through" : undefined,
            fontWeight: t.status === "in_progress" ? 600 : undefined,
          }}
        >
          <span aria-hidden>{TODO_MARK[t.status] ?? TODO_MARK.pending} </span>
          {t.content}
          {t.priority === "high" && <span className="small" style={{ color: warnColor }}> (high)</span>}
        </li>
      ))}
    </ul>
  );
}

export function CapabilityChips({ info }: { info: CapabilitiesInfo }) {
  const keys = [
    ...CAPABILITY_ORDER.filter((k) => k in info.effective),
    ...Object.keys(info.effective).filter((k) => !CAPABILITY_ORDER.includes(k)),
  ];
  return (
    <div className="harness-capabilities">
      {info.warnings.map((w, i) => (
        <div
          key={i}
          role="alert"
          style={{
            margin: "2px 0 6px", padding: "6px 10px", borderRadius: 6, fontWeight: 600,
            color: "#92400e", background: "#fef3c7", border: `1px solid ${warnColor}`,
          }}
        >
          ⚠ {w}
        </div>
      ))}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {keys.map((k) => {
          const v = info.effective[k];
          const off = v === "off";
          return (
            <span
              key={k}
              className="tag"
              data-capability={k}
              data-value={v}
              style={{ opacity: off ? 0.55 : 1 }}
            >
              {k}: {v}
            </span>
          );
        })}
        {info.model && <span className="small muted mono">{info.model}</span>}
      </div>
    </div>
  );
}

/**
 * Structured body for a harness signal, or null when the signal is not a
 * harness type or its detail cannot be parsed (the caller then falls back to
 * the plain summary / raw detail).
 */
export function renderHarnessBody(signal: Pick<Signal, "type" | "detail" | "summary">): ReactNode {
  switch (signal.type) {
    case "TOOL_CALL": {
      const info = parseToolCall(signal.detail);
      return info ? <ToolCallRow info={info} summary={signal.summary} /> : null;
    }
    case "AGENT_RESULT": {
      const info = parseAgentResult(signal.detail);
      return info ? <AgentResultCard info={info} /> : null;
    }
    case "AGENT_TODO": {
      const items = parseTodos(signal.detail);
      return items ? <TodoList items={items} /> : null;
    }
    case "CAPABILITIES": {
      const info = parseCapabilities(signal.detail);
      return info ? <CapabilityChips info={info} /> : null;
    }
    case "THINKING":
      return signal.detail ? <CollapsibleText text={signal.detail} /> : null;
    default:
      return null;
  }
}
