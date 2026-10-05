import { useState, useEffect } from "react";
import { getToken } from "../api";
import type { Signal } from "../types";
import { fieldCls } from "../lib/field";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

interface FileEntry {
  path: string;
  status: "claimed" | "written" | "deleted";
  agent: string;
  claimedAt?: string;
}

function buildFileTree(signals: Signal[]): Map<string, FileEntry> {
  const files = new Map<string, FileEntry>();
  for (const sig of signals) {
    if (sig.type === "FILE_CLAIM") {
      let detail: Record<string, string> = {};
      try { detail = JSON.parse(sig.detail || "{}"); } catch { /* ignore */ }
      const path = detail.path || sig.summary.replace(/^[^:]+: claim: /, "");
      files.set(path, {
        path,
        status: "claimed",
        agent: sig.from_agent || "unknown",
        claimedAt: sig.timestamp,
      });
    } else if (sig.type === "FILE_RELEASE") {
      let detail: Record<string, string> = {};
      try { detail = JSON.parse(sig.detail || "{}"); } catch { /* ignore */ }
      const path = detail.path || sig.summary.replace(/^[^:]+: release: /, "");
      const outcome = detail.outcome || "written";
      const existing = files.get(path);
      files.set(path, {
        path,
        status: outcome === "deleted" ? "deleted" : "written",
        agent: existing?.agent || sig.from_agent || "unknown",
      });
    }
  }
  return files;
}

function groupByDir(files: Map<string, FileEntry>): Map<string, FileEntry[]> {
  const dirs = new Map<string, FileEntry[]>();
  for (const entry of files.values()) {
    const parts = entry.path.split("/");
    const dir = parts.length > 1 ? parts.slice(0, -1).join("/") : "(root)";
    if (!dirs.has(dir)) dirs.set(dir, []);
    dirs.get(dir)!.push(entry);
  }
  // Sort each directory's files alphabetically
  for (const entries of dirs.values()) {
    entries.sort((a, b) => a.path.localeCompare(b.path));
  }
  return new Map([...dirs.entries()].sort(([a], [b]) => a.localeCompare(b)));
}

// Agents get a stable tone from the status tokens, chosen by hashing the role.
const AGENT_TONES = [
  { text: "text-status-waiting", soft: "bg-status-waiting/10", dot: "bg-status-waiting" },
  { text: "text-status-running", soft: "bg-status-running/10", dot: "bg-status-running" },
  { text: "text-status-blocked", soft: "bg-status-blocked/10", dot: "bg-status-blocked" },
  { text: "text-status-complete", soft: "bg-status-complete/10", dot: "bg-status-complete" },
  { text: "text-status-failed", soft: "bg-status-failed/10", dot: "bg-status-failed" },
  { text: "text-muted-foreground", soft: "bg-muted", dot: "bg-muted-foreground" },
];

function agentTone(role: string): (typeof AGENT_TONES)[number] {
  let h = 0;
  for (let i = 0; i < role.length; i++) h = (h * 31 + role.charCodeAt(i)) >>> 0;
  return AGENT_TONES[h % AGENT_TONES.length];
}

const DIR_CLS = "px-3 py-0.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground";

interface BranchFileEntry {
  path: string;
  type: "blob";
  size: number;
}

interface Props {
  signals: Signal[];
  runId?: string;
  onFileClick?: (path: string) => void;
}

export function FileTreeTab({ signals, runId, onFileClick }: Props) {
  const [branches, setBranches] = useState<string[]>([]);
  const [branchError, setBranchError] = useState<string | null>(null);
  const [selectedBranch, setSelectedBranch] = useState<string>("__signals__");
  const [branchFiles, setBranchFiles] = useState<BranchFileEntry[] | null>(null);
  const [branchLoading, setBranchLoading] = useState(false);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const token = getToken();
    const fetchBranches = () =>
      fetch(`/api/runs/${encodeURIComponent(runId)}/git/branches`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      }).then(async (r) => {
        if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status });
        return r.json() as Promise<string[]>;
      });

    fetchBranches()
      .then((bs) => { if (!cancelled) { setBranches(bs); setBranchError(null); } })
      .catch((e) => {
        if (cancelled) return;
        if ((e as { status?: number }).status === 404) {
          // Retry once after 2 s — covers uvicorn still reloading on page mount
          const tid = setTimeout(() => {
            fetchBranches()
              .then((bs) => { if (!cancelled) { setBranches(bs); setBranchError(null); } })
              .catch(() => {
                if (!cancelled) setBranchError("404");
              });
          }, 2000);
          return () => clearTimeout(tid);
        }
        setBranchError(String(e?.message ?? e));
      });
    return () => { cancelled = true; };
  }, [runId]);

  useEffect(() => {
    if (!runId || selectedBranch === "__signals__") {
      setBranchFiles(null);
      return;
    }
    setBranchLoading(true);
    const token = getToken();
    fetch(
      `/api/runs/${encodeURIComponent(runId)}/git/tree?branch=${encodeURIComponent(selectedBranch)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    )
      .then((r) => (r.ok ? r.json() as Promise<BranchFileEntry[]> : []))
      .then((fs) => setBranchFiles(fs))
      .catch(() => setBranchFiles([]))
      .finally(() => setBranchLoading(false));
  }, [runId, selectedBranch]);

  // Branch view
  if (selectedBranch !== "__signals__" && runId) {
    const grouped = groupBranchFiles(branchFiles ?? []);
    return (
      <div className="font-mono text-[13px]">
        <BranchPicker
          branches={branches}
          selected={selectedBranch}
          onChange={setSelectedBranch}
          error={branchError}
          showRunId={!!runId}
        />
        {branchLoading ? (
          <p className="m-0 p-3 text-xs text-muted-foreground">Loading…</p>
        ) : branchFiles === null || branchFiles.length === 0 ? (
          <p className="m-0 p-3 text-xs text-muted-foreground">No files on this branch yet.</p>
        ) : (
          <div className="py-2">
            {[...grouped.entries()].map(([dir, paths]) => (
              <div key={dir} className="mb-1">
                <div className={DIR_CLS}>{dir}/</div>
                {paths.map((p) => (
                  <div
                    key={p}
                    className={cn("flex items-center gap-2 py-[3px] pl-6 pr-3", onFileClick && "cursor-pointer")}
                    onClick={onFileClick ? () => onFileClick(p) : undefined}
                  >
                    <span className="shrink-0 text-status-complete">✓</span>
                    <span className="flex-1 text-foreground">{p.split("/").pop()}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // Signal-derived view (default)
  const files = buildFileTree(signals);
  const grouped = groupByDir(files);

  if (files.size === 0) {
    // No agent has touched a file yet. If main exists (seeded from
    // imports), nudge the user to switch to it so they can see the
    // imported repo state instead of staring at an empty pane.
    const hasMain = branches.includes("main") || branches.includes("master");
    return (
      <div className="font-mono text-[13px]">
        {runId && (
          <BranchPicker
            branches={branches}
            selected={selectedBranch}
            onChange={setSelectedBranch}
            error={branchError}
            showRunId={!!runId}
          />
        )}
        <div className="p-3">
          <p className="mb-2 mt-0 text-xs text-muted-foreground">
            No files written yet by agents.
          </p>
          {hasMain && (
            <Button
              variant="outline"
              onClick={() => setSelectedBranch(branches.includes("main") ? "main" : "master")}
            >
              View imported / seeded files on main →
            </Button>
          )}
        </div>
      </div>
    );
  }

  const activeClaims = [...files.values()].filter((f) => f.status === "claimed");
  const writtenCount = [...files.values()].filter((f) => f.status === "written").length;

  return (
    <div className="font-mono text-[13px]">
      {runId && (
        <BranchPicker
          branches={branches}
          selected={selectedBranch}
          onChange={setSelectedBranch}
          error={branchError}
          showRunId={!!runId}
        />
      )}
      {/* Summary bar */}
      <div className="flex flex-wrap items-center gap-4 border-b border-border bg-muted px-3 py-1.5">
        <span className="text-xs text-muted-foreground">{files.size} files tracked</span>
        <span className="text-xs text-status-complete">✓ {writtenCount} written</span>
        {activeClaims.length > 0 && (
          <span className="text-xs text-status-blocked">✎ {activeClaims.length} in progress</span>
        )}
      </div>

      {/* Active claims banner */}
      {activeClaims.length > 0 && (
        <div className="border-b border-status-blocked/30 bg-status-blocked/10 px-3 py-2">
          {activeClaims.map((f) => {
            const tone = agentTone(f.agent);
            return (
              <div key={f.path} className="mb-0.5 flex items-start gap-2">
                <span className={cn("mt-1 inline-block h-2 w-2 shrink-0 animate-pulse rounded-full", tone.dot)} />
                <span className={cn("font-semibold", tone.text)}>{f.agent}</span>
                <span className="text-foreground">is writing</span>
                <span className="text-status-running">{f.path}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Directory tree */}
      <div className="py-2">
        {[...grouped.entries()].map(([dir, entries]) => (
          <div key={dir} className="mb-1">
            <div className={DIR_CLS}>{dir}/</div>
            {entries.map((f) => {
              const filename = f.path.split("/").pop()!;
              const isClaimed = f.status === "claimed";
              const isDeleted = f.status === "deleted";
              const tone = agentTone(f.agent);
              return (
                <div
                  key={f.path}
                  className={cn(
                    "flex items-center gap-2 py-[3px] pl-6 pr-3",
                    isClaimed && "bg-status-blocked/10",
                    isDeleted && "opacity-40",
                    onFileClick && !isDeleted && "cursor-pointer",
                  )}
                  onClick={onFileClick && !isDeleted ? () => onFileClick(f.path) : undefined}
                  title={isClaimed ? `${f.agent} is writing this file` : undefined}
                >
                  <span
                    className={cn(
                      "shrink-0",
                      isClaimed ? "text-status-blocked" : isDeleted ? "text-status-failed" : "text-status-complete",
                    )}
                  >
                    {isClaimed ? "✎" : isDeleted ? "✗" : "✓"}
                  </span>
                  <span
                    className={cn(
                      "flex-1",
                      isDeleted && "line-through",
                      isClaimed ? "font-semibold text-status-blocked" : "text-foreground",
                    )}
                  >
                    {filename}
                  </span>
                  <span className={cn("shrink-0 rounded px-1.5 py-px text-xs", tone.text, tone.soft)}>
                    {f.agent}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function BranchPicker({
  branches,
  selected,
  onChange,
  error,
  showRunId,
}: {
  branches: string[];
  selected: string;
  onChange: (b: string) => void;
  error?: string | null;
  showRunId?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-1.5">
      <span className="text-xs text-muted-foreground">Branch:</span>
      <select
        value={selected}
        onChange={(e) => onChange(e.target.value)}
        className={cn(fieldCls, "w-auto px-1.5 py-0.5 text-xs")}
      >
        <option value="__signals__">All (signals view)</option>
        {branches.map((b) => (
          <option key={b} value={b}>{b}</option>
        ))}
      </select>
      {error ? (
        <span className="text-xs text-muted-foreground">
          {error === "404"
            ? "Branch picker not available on this server."
            : `Could not load branches (${error})`}
        </span>
      ) : branches.length === 0 && showRunId ? (
        <span className="text-xs text-muted-foreground">
          No agent branches yet — agents haven't committed.
        </span>
      ) : null}
    </div>
  );
}

function groupBranchFiles(files: { path: string }[]): Map<string, string[]> {
  const dirs = new Map<string, string[]>();
  for (const f of files) {
    const parts = f.path.split("/");
    const dir = parts.length > 1 ? parts.slice(0, -1).join("/") : "(root)";
    if (!dirs.has(dir)) dirs.set(dir, []);
    dirs.get(dir)!.push(f.path);
  }
  return new Map([...dirs.entries()].sort(([a], [b]) => a.localeCompare(b)));
}
