import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TypedSignal } from "../../lib/runEvents.gen";
import { cn } from "../../lib/utils";
import { useRunStore } from "../../stores/runStoreContext";
import { fetchRoleChanges } from "../report/api";
import { DiffText } from "../report/DiffViewer";

/** opencode tools that can change files; a finished call of one refreshes the view. */
export const EDIT_TOOLS = new Set(["edit", "write", "patch", "multiedit", "apply_patch", "bash"]);
const MIN_GAP_MS = 2_000;
const POLL_MS = 10_000;

/** Id of the role's latest finished file-changing tool call, or "" if none. */
export function lastEditId(feed: readonly TypedSignal[] | undefined): string {
  if (!feed) return "";
  for (let i = feed.length - 1; i >= 0; i -= 1) {
    const tool = (feed[i].data as { tool?: unknown } | null | undefined)?.tool;
    if (typeof tool === "string" && EDIT_TOOLS.has(tool)) return feed[i].id;
  }
  return "";
}

const STATUS_LABEL: Record<string, string> = { M: "modified", A: "added", D: "deleted", untracked: "new" };

/** What a role has written since it last synced with main, refreshed as it edits files. */
export function RoleChanges({ role }: { role: string }) {
  const runId = useRunStore((s) => s.runId);
  const running = useRunStore((s) => s.live && s.agents[role]?.state === "running");
  const name = useRunStore((s) => s.names[role]);
  const lastEdit = useRunStore((s) => lastEditId(s.toolFeed[role]));
  const q = useQuery({
    queryKey: ["runs", runId, "role-changes", role] as const,
    queryFn: () => fetchRoleChanges(runId, role),
    refetchInterval: running ? POLL_MS : false,
  });

  // Refresh after each edit, at most once per MIN_GAP_MS (the mount already fetched).
  const seen = useRef(lastEdit);
  const { refetch, dataUpdatedAt } = q;
  useEffect(() => {
    if (lastEdit === seen.current) return;
    seen.current = lastEdit;
    const wait = Math.max(0, MIN_GAP_MS - (Date.now() - dataUpdatedAt));
    const t = setTimeout(() => void refetch(), wait);
    return () => clearTimeout(t);
  }, [lastEdit, refetch, dataUpdatedAt]);

  if (q.isPending) return <p className="m-0 p-3 text-sm text-muted-foreground" role="status">Loading changes…</p>;
  if (q.isError) {
    return (
      <p className="m-0 p-3 text-sm text-muted-foreground" role="status">
        No working copy to show for {role} yet ({q.error.message}).
      </p>
    );
  }
  const { files, diff, truncated } = q.data;
  const added = files.reduce((n, f) => n + f.added, 0);
  const removed = files.reduce((n, f) => n + f.removed, 0);
  return (
    <div className={cn("min-h-0 flex-1 overflow-y-auto", !running && "opacity-70")} data-testid={`role-changes-${role}`}>
      <div className="px-3 py-1 text-xs text-muted-foreground">
        Since {name ?? role} last synced with main · {files.length} file{files.length === 1 ? "" : "s"}{" "}
        <span className="text-status-complete">+{added}</span> <span className="text-status-failed">−{removed}</span>
        {!running && " · not working right now; merged turns are under the card's diffs"}
      </div>
      {files.length === 0 ? (
        <p className="m-0 px-3 py-2 text-sm text-muted-foreground">No changes yet.</p>
      ) : (
        <>
          <ul className="m-0 list-none border-y border-border px-3 py-1 font-mono text-xs">
            {files.map((f) => (
              <li key={f.path} className="flex gap-2">
                <span className="min-w-0 flex-1 truncate">{f.path}</span>
                <span className="text-muted-foreground">{STATUS_LABEL[f.status] ?? f.status}</span>
                <span className="text-status-complete">+{f.added}</span>
                <span className="text-status-failed">−{f.removed}</span>
              </li>
            ))}
          </ul>
          <DiffText text={diff} />
          {truncated && <p className="m-0 px-3 py-1 text-xs text-muted-foreground">Diff truncated.</p>}
        </>
      )}
    </div>
  );
}
