// Minimal renderers for the signals the opencode harness posts. All content is
// rendered as React text nodes (never as HTML).

import { useState, type ReactNode } from "react";
import type { Signal } from "../../types";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
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

type Tone = "complete" | "failed" | "blocked";

function ResultBadge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <Badge variant={tone} title={title} className="shrink-0 whitespace-nowrap font-semibold">
      {children}
    </Badge>
  );
}

const PRE_CLS = "m-0 mt-1.5 overflow-x-auto rounded bg-muted p-2 font-mono text-[11px] text-foreground";

/** Long text collapsed behind a <details>; short text shown directly. */
export function CollapsibleText({ text, label = "Show full text" }: { text: string; label?: string }) {
  if (text.length <= LONG_TEXT_CHARS) return <pre className={PRE_CLS}>{text}</pre>;
  return (
    <details className="harness-collapsible">
      <summary className="cursor-pointer text-xs text-muted-foreground">
        {label} ({text.length} chars): {text.slice(0, 80).replace(/\s+/g, " ")}…
      </summary>
      <pre className={PRE_CLS}>{text}</pre>
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
        className="flex w-full cursor-pointer items-center gap-2 border-0 bg-transparent px-0 py-0.5 text-left text-inherit"
        title={summary}
      >
        <span className="text-xs text-muted-foreground">{open ? "▾" : "▸"}</span>
        <strong className="shrink-0 whitespace-nowrap font-mono">{info.tool}</strong>
        {info.target && (
          <span className="min-w-0 max-w-[360px] truncate font-mono text-xs text-muted-foreground">
            {info.target}
          </span>
        )}
        <ResultBadge tone={info.ok ? "complete" : "failed"}>{info.ok ? "ok" : "error"}</ResultBadge>
        {info.durationMs !== null && <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">{formatDuration(info.durationMs)}</span>}
      </button>
      {open && (
        <div className="ml-4">
          <div className="text-xs text-muted-foreground">Input</div>
          <pre className={PRE_CLS}>{info.input || "(none)"}</pre>
          <div className="text-xs text-muted-foreground">{info.ok ? "Output" : "Error"}</div>
          <pre className={PRE_CLS}>{body || "(none)"}</pre>
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
  const tone: Tone = good ? "complete" : bad ? "failed" : "blocked";

  return (
    <div className="harness-agent-result">
      <div className="flex items-center gap-2">
        {label && <ResultBadge tone={tone}>{label}</ResultBadge>}
        {stats.length > 0 && <span className="text-xs text-muted-foreground">{stats.join(" · ")}</span>}
      </div>
      {info.summary && <p className="my-1">{info.summary}</p>}
      {reviewer && info.issues.length > 0 && (
        <ul aria-label="Review issues" className="my-1 pl-[18px]">
          {info.issues.map((i, n) => (
            <li key={n}>
              <span className="font-mono text-xs">{i.file}</span>: {i.detail}
            </li>
          ))}
        </ul>
      )}
      {info.files.length > 0 && (
        <div className="text-xs">
          <span className="text-muted-foreground">Files changed ({info.files.length}): </span>
          <span className="font-mono">{info.files.join(", ")}</span>
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
    <ul aria-label="Agent todos" className="my-1 list-none p-0">
      {items.map((t, i) => (
        <li
          key={i}
          data-status={t.status}
          className={cn(
            (t.status === "completed" || t.status === "cancelled") && "line-through",
            t.status === "in_progress" && "font-semibold",
          )}
        >
          <span aria-hidden>{TODO_MARK[t.status] ?? TODO_MARK.pending} </span>
          {t.content}
          {t.priority === "high" && <span className="text-xs text-status-blocked"> (high)</span>}
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
          className="mb-1.5 mt-0.5 rounded-md border border-status-blocked bg-status-blocked/10 px-2.5 py-1.5 font-semibold text-status-blocked"
        >
          ⚠ {w}
        </div>
      ))}
      <div className="flex flex-wrap gap-1.5">
        {keys.map((k) => {
          const v = info.effective[k];
          const off = v === "off";
          return (
            <Badge
              key={k}
              variant="secondary"
              data-capability={k}
              data-value={v}
              className={cn(off && "opacity-55")}
            >
              {k}: {v}
            </Badge>
          );
        })}
        {info.model && <span className="font-mono text-xs text-muted-foreground">{info.model}</span>}
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
