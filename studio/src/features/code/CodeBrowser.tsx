import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Segmented } from "../../components/ui/segmented";
import { cn } from "../../lib/utils";
import { DiffText } from "../report/DiffViewer";
import {
  fetchCodeFile, fetchCodeRoles, fetchCodeTree,
  type CodeSource, type OpenCodeDetail,
} from "./api";
import { buildTree, parentsOf, type TreeNode } from "./model";
import { DependencyList } from "./DependencyList";

const POLL_MS = 5_000;
const MIN_GAP_MS = 1_500;
const STATUS_TONE: Record<string, string> = {
  A: "text-status-complete", M: "text-status-waiting", D: "text-status-failed",
};
const SELECT = "h-7 rounded-md border border-input bg-background px-2 text-xs";

function sourceKey(s: CodeSource): string {
  return s.kind === "role" ? `role:${s.role}` : s.kind;
}

/**
 * The Code tab (plan §15): whose code (the accepted project, this session's main, or one
 * worker's checkout), a file tree with what changed, and one file as text or as a diff.
 *
 * `editKeys` (role → id of its latest file edit) refreshes a worker's view right after it
 * edits; `names` labels roles by worker name; `working` lists the roles working now; a new
 * `focus` object opens that file (from a card).
 */
export function CodeBrowser({
  projectId, runId, live = false, editKeys, names, working, focus, initialSource,
}: {
  projectId?: string;
  runId?: string;
  live?: boolean;
  editKeys?: Record<string, string>;
  names?: Record<string, string>;
  working?: readonly string[];
  focus?: OpenCodeDetail | null;
  /** Where to start (default: the project when given, else this session). */
  initialSource?: CodeSource;
}) {
  const [source, setSource] = useState<CodeSource>(
    initialSource ?? (projectId ? { kind: "project" } : { kind: "run" }));
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"file" | "diff">("file");
  const [changedOnly, setChangedOnly] = useState(initialSource?.kind === "role");
  const [follow, setFollow] = useState(true);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [showDeps, setShowDeps] = useState(false);
  const role = source.kind === "role" ? source.role : null;
  const polling = live && source.kind !== "project" ? POLL_MS : false;

  const roles = useQuery({
    queryKey: ["code", runId, "roles"] as const,
    queryFn: () => fetchCodeRoles(runId!),
    enabled: !!runId,
    refetchInterval: live ? 15_000 : false,
  });
  const tree = useQuery({
    queryKey: ["code", projectId, runId, sourceKey(source), "tree"] as const,
    queryFn: () => fetchCodeTree(projectId, runId, source),
    refetchInterval: polling,
  });
  const diffAllowed = source.kind !== "project";
  const showDiff = diffAllowed && view === "diff";
  const file = useQuery({
    queryKey: ["code", projectId, runId, sourceKey(source), "file", selected, showDiff] as const,
    queryFn: () => fetchCodeFile(projectId, runId, source, selected!, showDiff),
    enabled: !!selected,
    refetchInterval: polling,
  });

  // A worker's edit refreshes its tree and the open file (at most once per MIN_GAP_MS).
  const editKey = role ? editKeys?.[role] ?? "" : "";
  const seen = useRef(editKey);
  const { refetch: refetchTree, dataUpdatedAt } = tree;
  const { refetch: refetchFile } = file;
  useEffect(() => {
    if (editKey === seen.current) return;
    seen.current = editKey;
    const wait = Math.max(0, MIN_GAP_MS - (Date.now() - dataUpdatedAt));
    const t = setTimeout(() => {
      void refetchTree();
      if (selected) void refetchFile();
    }, wait);
    return () => clearTimeout(t);
  }, [editKey, refetchTree, refetchFile, dataUpdatedAt, selected]);

  // Follow edits: open the file the worker wrote last.
  const lastEdit = tree.data?.last_edit?.path ?? null;
  useEffect(() => {
    if (!follow || !role || !live || !lastEdit) return;
    setSelected(lastEdit);
    setOpen((o) => new Set([...o, ...parentsOf(lastEdit)]));
  }, [follow, role, live, lastEdit]);

  // Opening from a card: switch to that worker (or this session) and show the file's diff.
  useEffect(() => {
    if (!focus) return;
    if (runId) setSource(focus.role ? { kind: "role", role: focus.role } : { kind: "run" });
    setChangedOnly(!!focus.role);
    if (!focus.path) return;
    const path = focus.path;
    setSelected(path);
    setView("diff");
    setFollow(false);
    setOpen((o) => new Set([...o, ...parentsOf(path)]));
  }, [focus, runId]);

  const entries = useMemo(() => tree.data?.entries ?? [], [tree.data]);
  const root = useMemo(() => buildTree(entries, changedOnly), [entries, changedOnly]);
  const changedCount = entries.filter((e) => e.status).length;
  const selectedEntry = entries.find((e) => e.path === selected);

  // Small trees open fully; bigger ones open the folders holding changes.
  const autoOpen = useMemo(() => {
    const s = new Set<string>();
    const small = entries.length <= 60;
    for (const e of entries) if (small || e.status) for (const p of parentsOf(e.path)) s.add(p);
    return s;
  }, [entries]);
  const isOpen = (path: string) => open.has(path) || (autoOpen.has(path) && !open.has(`!${path}`));
  const toggle = (path: string) =>
    setOpen((o) => {
      const n = new Set(o);
      if (isOpen(path)) {
        n.delete(path);
        n.add(`!${path}`);
      } else {
        n.add(path);
        n.delete(`!${path}`);
      }
      return n;
    });

  const pick = (s: CodeSource) => {
    setSource(s);
    setChangedOnly(s.kind === "role");
    setOpen(new Set());
  };
  const roleLabel = (r: string) =>
    `${names?.[r] ? `${names[r]} · ${r}` : r}${working?.includes(r) ? " · working now" : ""}`;
  const workerRoles = roles.data?.roles ?? [];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2" data-testid="code-browser">
      <div className="flex flex-wrap items-center gap-2 px-1">
        <div role="group" aria-label="Whose code" className="inline-flex items-center gap-0.5 rounded-md bg-muted p-0.5">
          {projectId && (
            <SourceButton on={source.kind === "project"} onClick={() => pick({ kind: "project" })}>
              Project · accepted
            </SourceButton>
          )}
          {runId && (
            <SourceButton on={source.kind === "run"} onClick={() => pick({ kind: "run" })}>
              This session
            </SourceButton>
          )}
        </div>
        {projectId && (
          <button type="button" aria-pressed={showDeps} onClick={() => setShowDeps((v) => !v)}
                  className={cn("cursor-pointer rounded-md border border-border px-2.5 py-0.5 text-xs",
                                showDeps ? "bg-accent text-foreground" : "bg-transparent text-muted-foreground hover:bg-accent")}>
            Dependencies
          </button>
        )}
        {runId && workerRoles.length > 0 && (
          <select
            aria-label="Worker"
            className={cn(SELECT, role && "border-primary")}
            value={role ?? ""}
            onChange={(e) => (e.target.value ? pick({ kind: "role", role: e.target.value }) : pick({ kind: "run" }))}
          >
            <option value="">Worker…</option>
            {workerRoles.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </select>
        )}
        <label className="flex items-center gap-1 text-xs text-muted-foreground">
          <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
          Changed only
        </label>
        {role && live && (
          <label className="flex items-center gap-1 text-xs text-muted-foreground">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
            Follow edits
          </label>
        )}
        {role && lastEdit && (
          <span className="text-xs text-muted-foreground">
            Last edit: <span className="font-mono">{lastEdit}</span>
          </span>
        )}
      </div>

      {showDeps && projectId ? (
        <div className="min-h-0 flex-1 overflow-hidden rounded-md border border-border">
          <DependencyList projectId={projectId} names={names} />
        </div>
      ) : (
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border sm:flex-row">
        <nav aria-label="Files" className="max-h-60 w-full shrink-0 overflow-auto border-b border-border p-1 sm:max-h-none sm:w-60 sm:border-b-0 sm:border-r">
          <p className="m-0 px-2 py-1 text-xs text-muted-foreground">
            {source.kind === "project" && "Accepted main"}
            {source.kind === "run" && `This session · ${changedCount} changed`}
            {role && `${names?.[role] ?? role}'s checkout · ${changedCount} unmerged`}
          </p>
          {tree.isPending ? (
            <p className="m-0 p-2 text-xs text-muted-foreground" role="status">Loading files…</p>
          ) : tree.isError ? (
            <p className="m-0 p-2 text-xs text-muted-foreground" role="status">No code to show yet ({tree.error.message}).</p>
          ) : root.children.length === 0 ? (
            <p className="m-0 p-2 text-xs text-muted-foreground">{changedOnly ? "Nothing changed." : "No files."}</p>
          ) : (
            <ul role="tree" className="m-0 list-none p-0">
              {root.children.map((n) => (
                <TreeItem key={n.path} node={n} depth={0} selected={selected} isOpen={isOpen}
                          onToggle={toggle} onPick={(p) => { setSelected(p); setFollow(false); }}
                          editing={role && live ? lastEdit : null} />
              ))}
            </ul>
          )}
          {tree.data?.truncated && <p className="m-0 px-2 py-1 text-xs text-muted-foreground">Only the first 5000 files are listed.</p>}
        </nav>

        <section aria-label="File" className="flex min-h-0 min-w-0 flex-1 flex-col">
          {selected ? (
            <>
              <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5">
                <span className="min-w-0 truncate font-mono text-xs">
                  {selectedEntry?.status && <span className={cn("mr-2", STATUS_TONE[selectedEntry.status])}>{selectedEntry.status}</span>}
                  {selected}
                </span>
                {diffAllowed && (
                  <Segmented label="View" value={view} onChange={setView}
                             options={[{ value: "file", label: "File" }, { value: "diff", label: "Diff" }]} />
                )}
              </div>
              <div className="min-h-0 flex-1 overflow-auto">
                <FileBody q={file} showDiff={showDiff} />
              </div>
            </>
          ) : (
            <p className="m-0 p-4 text-sm text-muted-foreground">Pick a file to read it.</p>
          )}
        </section>
      </div>
      )}
    </div>
  );
}

function SourceButton({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "cursor-pointer rounded-sm border border-transparent px-2.5 py-0.5 text-xs font-medium",
        on ? "bg-background text-foreground shadow-sm" : "bg-transparent text-muted-foreground hover:bg-accent",
      )}
    >
      {children}
    </button>
  );
}

function TreeItem({
  node, depth, selected, isOpen, onToggle, onPick, editing,
}: {
  node: TreeNode;
  depth: number;
  selected: string | null;
  isOpen: (path: string) => boolean;
  onToggle: (path: string) => void;
  onPick: (path: string) => void;
  editing: string | null;
}) {
  const pad = { paddingLeft: `${depth * 12 + 8}px` };
  if (!node.entry) {
    const opened = isOpen(node.path);
    return (
      <li role="treeitem" aria-expanded={opened} aria-selected={false}>
        <button type="button" onClick={() => onToggle(node.path)} style={pad}
                className="flex w-full cursor-pointer items-center gap-1 rounded-sm bg-transparent py-0.5 pr-2 text-left text-xs text-muted-foreground hover:bg-accent">
          <span aria-hidden>{opened ? "▾" : "▸"}</span>
          <span className="truncate">{node.name}</span>
          {node.changed > 0 && <span className="ml-auto text-[10px]">{node.changed}</span>}
        </button>
        {opened && (
          <ul role="group" className="m-0 list-none p-0">
            {node.children.map((c) => (
              <TreeItem key={c.path} node={c} depth={depth + 1} selected={selected} isOpen={isOpen}
                        onToggle={onToggle} onPick={onPick} editing={editing} />
            ))}
          </ul>
        )}
      </li>
    );
  }
  const e = node.entry;
  const on = selected === node.path;
  return (
    <li role="treeitem" aria-selected={on}>
      <button type="button" onClick={() => onPick(node.path)} style={pad}
              className={cn("flex w-full cursor-pointer items-center gap-1 rounded-sm bg-transparent py-0.5 pr-2 text-left text-xs hover:bg-accent",
                            on && "bg-accent text-foreground", e.status === "D" && "line-through opacity-70")}>
        <span className={cn("w-3 shrink-0 font-mono text-[10px]", STATUS_TONE[e.status])}>{e.status}</span>
        <span className="truncate">{node.name}</span>
        {editing === node.path && <span className="ml-auto text-[10px] text-primary">● editing</span>}
      </button>
    </li>
  );
}

function FileBody({ q, showDiff }: {
  q: ReturnType<typeof useQuery<Awaited<ReturnType<typeof fetchCodeFile>>>>;
  showDiff: boolean;
}) {
  if (q.isPending) return <p className="m-0 p-3 text-sm text-muted-foreground" role="status">Loading…</p>;
  if (q.isError) return <p className="m-0 p-3 text-sm text-destructive" role="alert">Could not load the file: {q.error.message}</p>;
  const f = q.data;
  if (showDiff) return f.diff ? <DiffText text={f.diff} /> : <p className="m-0 p-3 text-sm text-muted-foreground">No changes to this file.</p>;
  if (f.deleted) return <p className="m-0 p-3 text-sm text-muted-foreground">This file was deleted. Switch to Diff to see what it held.</p>;
  if (f.binary) return <p className="m-0 p-3 text-sm text-muted-foreground">Binary file ({f.size} bytes), not shown.</p>;
  if (f.too_large) return <p className="m-0 p-3 text-sm text-muted-foreground">Too large to show ({Math.round(f.size / 1000)} KB).</p>;
  const lines = (f.text ?? "").replace(/\n$/, "").split("\n");
  return (
    <pre className="m-0 p-0 font-mono text-xs leading-5" data-testid="code-file">
      {lines.map((ln, i) => (
        <div key={i} className="flex">
          <span className="w-10 shrink-0 select-none pr-3 text-right text-muted-foreground">{i + 1}</span>
          <span className="whitespace-pre">{ln}</span>
        </div>
      ))}
    </pre>
  );
}
