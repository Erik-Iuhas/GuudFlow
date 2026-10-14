import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getConsole } from "../../api";
import { cn } from "../../lib/utils";

const LEVEL_CLASS: Record<string, string> = {
  error: "text-status-failed", critical: "text-status-failed", warning: "text-status-blocked",
};

/**
 * What GuudFlow itself was doing for the run (scheduler, spawns, errors with tracebacks) and each
 * role's opencode server log. The board shows what agents did; this shows why something did not
 * happen, e.g. a run that ended before its first model call.
 */
export function ConsolePanel({ runId, live }: { runId: string; live: boolean }) {
  const [source, setSource] = useState("all");
  const q = useQuery({
    queryKey: ["run-console", runId],
    queryFn: () => getConsole(runId),
    refetchInterval: live ? 3000 : false,
  });
  const lines = q.data?.lines ?? [];
  const sources = ["all", ...Array.from(new Set(lines.map((l) => l.source))), ...(q.data?.roles ?? []).map((r) => `log: ${r.role}`)];
  const roleLog = (q.data?.roles ?? []).find((r) => `log: ${r.role}` === source);
  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2" data-testid="console-panel">
      <div className="flex items-center gap-2 text-xs">
        <label htmlFor="console-source" className="text-muted-foreground">Source</label>
        <select id="console-source" value={source} onChange={(e) => setSource(e.target.value)}
          className="rounded border border-border bg-background px-2 py-1">
          {sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {q.isError && <span className="text-status-failed">could not load the console</span>}
      </div>
      <pre className="m-0 min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-card p-2 font-mono text-xs">
        {roleLog
          ? roleLog.lines.join("\n")
          : lines.length === 0
            ? "Nothing logged yet."
            : lines.filter((l) => source === "all" || l.source === source).map((l, i) => (
              <span key={i} className={cn("block", LEVEL_CLASS[l.level])}>
                {new Date(l.t * 1000).toLocaleTimeString()} [{l.source}] {l.text}
              </span>
            ))}
      </pre>
    </div>
  );
}
