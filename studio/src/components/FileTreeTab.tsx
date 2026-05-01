import { useState, useEffect } from "react";
import { getToken } from "../api";
import type { Signal } from "../types";

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

const AGENT_COLORS: Record<string, string> = {
  game_designer: "#7c3aed",
  pygame_backend_dev: "#0369a1",
  pygame_frontend_dev: "#b45309",
  meta_agent: "#374151",
};

function agentColor(role: string): string {
  return AGENT_COLORS[role] ?? "#475569";
}

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
      <div style={{ fontFamily: "monospace", fontSize: 13 }}>
        <BranchPicker
          branches={branches}
          selected={selectedBranch}
          onChange={setSelectedBranch}
          error={branchError}
          showRunId={!!runId}
        />
        {branchLoading ? (
          <p className="muted small" style={{ padding: 12 }}>Loading…</p>
        ) : branchFiles === null || branchFiles.length === 0 ? (
          <p className="muted small" style={{ padding: 12 }}>No files on this branch yet.</p>
        ) : (
          <div style={{ padding: "8px 0" }}>
            {[...grouped.entries()].map(([dir, paths]) => (
              <div key={dir} style={{ marginBottom: 4 }}>
                <div style={{ padding: "2px 12px", color: "#64748b", fontWeight: 700, fontSize: 11, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  {dir}/
                </div>
                {paths.map((p) => (
                  <div
                    key={p}
                    className="row"
                    style={{ padding: "3px 12px 3px 24px", gap: 8, cursor: onFileClick ? "pointer" : "default" }}
                    onClick={onFileClick ? () => onFileClick(p) : undefined}
                  >
                    <span style={{ color: "#16a34a", flexShrink: 0 }}>✓</span>
                    <span style={{ flex: 1, color: "#1e293b" }}>{p.split("/").pop()}</span>
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
      <div style={{ fontFamily: "monospace", fontSize: 13 }}>
        {runId && (
          <BranchPicker
            branches={branches}
            selected={selectedBranch}
            onChange={setSelectedBranch}
            error={branchError}
            showRunId={!!runId}
          />
        )}
        <div style={{ padding: 12 }}>
          <p className="muted small" style={{ marginBottom: 8 }}>
            No files written yet by agents.
          </p>
          {hasMain && (
            <button
              onClick={() => setSelectedBranch(branches.includes("main") ? "main" : "master")}
            >
              View imported / seeded files on main →
            </button>
          )}
        </div>
      </div>
    );
  }

  const activeClaims = [...files.values()].filter((f) => f.status === "claimed");
  const writtenCount = [...files.values()].filter((f) => f.status === "written").length;

  return (
    <div style={{ fontFamily: "monospace", fontSize: 13 }}>
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
      <div
        className="row"
        style={{
          padding: "6px 12px",
          background: "#f8fafc",
          borderBottom: "1px solid #e2e8f0",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <span className="small muted">{files.size} files tracked</span>
        <span className="small" style={{ color: "#16a34a" }}>
          ✓ {writtenCount} written
        </span>
        {activeClaims.length > 0 && (
          <span className="small" style={{ color: "#b45309" }}>
            ✎ {activeClaims.length} in progress
          </span>
        )}
      </div>

      {/* Active claims banner */}
      {activeClaims.length > 0 && (
        <div style={{ padding: "8px 12px", borderBottom: "1px solid #fde68a", background: "#fefce8" }}>
          {activeClaims.map((f) => (
            <div key={f.path} className="row" style={{ gap: 8, marginBottom: 2 }}>
              <span
                style={{
                  display: "inline-block",
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: agentColor(f.agent),
                  animation: "pulse 1.5s infinite",
                  flexShrink: 0,
                  marginTop: 4,
                }}
              />
              <span style={{ color: agentColor(f.agent), fontWeight: 600 }}>{f.agent}</span>
              <span style={{ color: "#374151" }}>is writing</span>
              <span style={{ color: "#1e40af" }}>{f.path}</span>
            </div>
          ))}
        </div>
      )}

      {/* Directory tree */}
      <div style={{ padding: "8px 0" }}>
        {[...grouped.entries()].map(([dir, entries]) => (
          <div key={dir} style={{ marginBottom: 4 }}>
            <div
              style={{
                padding: "2px 12px",
                color: "#64748b",
                fontWeight: 700,
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              {dir}/
            </div>
            {entries.map((f) => {
              const filename = f.path.split("/").pop()!;
              const isClaimed = f.status === "claimed";
              const isDeleted = f.status === "deleted";
              return (
                <div
                  key={f.path}
                  className="row"
                  style={{
                    padding: "3px 12px 3px 24px",
                    gap: 8,
                    background: isClaimed ? "#fefce8" : "transparent",
                    opacity: isDeleted ? 0.4 : 1,
                    cursor: onFileClick && !isDeleted ? "pointer" : "default",
                  }}
                  onClick={onFileClick && !isDeleted ? () => onFileClick(f.path) : undefined}
                  title={isClaimed ? `${f.agent} is writing this file` : undefined}
                >
                  <span style={{ color: isClaimed ? "#d97706" : isDeleted ? "#ef4444" : "#16a34a", flexShrink: 0 }}>
                    {isClaimed ? "✎" : isDeleted ? "✗" : "✓"}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      textDecoration: isDeleted ? "line-through" : "none",
                      color: isClaimed ? "#92400e" : "#1e293b",
                      fontWeight: isClaimed ? 600 : 400,
                    }}
                  >
                    {filename}
                  </span>
                  <span
                    className="small"
                    style={{
                      color: agentColor(f.agent),
                      background: `${agentColor(f.agent)}18`,
                      padding: "1px 6px",
                      borderRadius: 4,
                      flexShrink: 0,
                    }}
                  >
                    {f.agent}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
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
    <div style={{ padding: "6px 12px", borderBottom: "1px solid #e2e8f0", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <span className="muted small">Branch:</span>
      <select
        value={selected}
        onChange={(e) => onChange(e.target.value)}
        style={{ fontSize: 12, padding: "2px 6px", borderRadius: 6, border: "1px solid #cbd5e1" }}
      >
        <option value="__signals__">All (signals view)</option>
        {branches.map((b) => (
          <option key={b} value={b}>{b}</option>
        ))}
      </select>
      {error ? (
        <span className="muted small">
          {error === "404"
            ? "Branch picker not available on this server."
            : `Could not load branches (${error})`}
        </span>
      ) : branches.length === 0 && showRunId ? (
        <span className="muted small">
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
