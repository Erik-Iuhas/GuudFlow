import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Diff, Hunk, parseDiff } from "react-diff-view";
import "react-diff-view/style/index.css";
import { fetchCommitDiff } from "./api";

/** Parse a unified diff; a body git could not render (or empty) yields no files. */
export function safeParseDiff(text: string): ReturnType<typeof parseDiff> {
  try {
    return parseDiff(text);
  } catch {
    return [];
  }
}

/** Render unified diff text with react-diff-view. */
export function DiffText({ text }: { text: string }) {
  const files = useMemo(() => safeParseDiff(text), [text]);
  if (files.length === 0) {
    return <pre className="m-0 whitespace-pre-wrap p-3 text-xs text-muted-foreground">{text || "(empty diff)"}</pre>;
  }
  return (
    <div className="diff-viewer overflow-x-auto text-xs" data-testid="diff-viewer">
      {files.map((f) => (
        <section key={`${f.oldRevision}-${f.newRevision}-${f.newPath}`} aria-label={f.newPath || f.oldPath}>
          <h4 className="m-0 border-b border-border bg-muted px-3 py-1 font-mono text-xs font-medium">
            {f.type === "delete" ? f.oldPath : f.newPath}
            <span className="ml-2 text-muted-foreground">{f.type}</span>
          </h4>
          <Diff viewType="unified" diffType={f.type} hunks={f.hunks}>
            {(hunks) => hunks.map((h) => <Hunk key={h.content} hunk={h} />)}
          </Diff>
        </section>
      ))}
    </div>
  );
}

/** One commit's diff, loaded from `GET /api/runs/{id}/git/diff?ref=`. */
export function CommitDiff({ runId, sha }: { runId: string; sha: string }) {
  const q = useQuery({
    queryKey: ["runs", runId, "diff", sha] as const,
    queryFn: () => fetchCommitDiff(runId, sha),
    staleTime: Infinity,
  });
  if (q.isPending) return <p className="m-0 p-3 text-sm text-muted-foreground" role="status">Loading diff…</p>;
  if (q.isError) return <p className="m-0 p-3 text-sm text-destructive" role="alert">Could not load the diff: {q.error.message}</p>;
  return <DiffText text={q.data.diff} />;
}
