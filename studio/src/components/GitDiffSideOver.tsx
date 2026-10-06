import { useEffect, useState } from "react";
import { getToken } from "../api";
import { errorCls } from "../lib/field";
import { cn } from "../lib/utils";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "./ui/sheet";

interface Props {
  runId: string;
  sha: string | null;
  onClose: () => void;
}

interface DiffData {
  sha: string;
  diff: string;
}

export function GitDiffSideOver({ runId, sha, onClose }: Props) {
  const [data, setData] = useState<DiffData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sha) return;
    setLoading(true);
    setError(null);
    setData(null);
    const token = getToken();
    fetch(
      `/api/runs/${encodeURIComponent(runId)}/git/diff?ref=${encodeURIComponent(sha)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    )
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<DiffData>;
      })
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [runId, sha]);

  if (!sha) return null;

  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[600px] max-w-full gap-0 p-0">
        <div className="flex shrink-0 items-center border-b border-border px-5 py-4 pr-12">
          <SheetTitle className="text-base">Commit diff</SheetTitle>
          <SheetDescription className="ml-2 font-mono text-xs text-muted-foreground">{sha.slice(0, 7)}</SheetDescription>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {loading && <div className="p-4 text-xs text-muted-foreground">Loading diff…</div>}
          {error && <div className={cn(errorCls, "m-4")}>{error}</div>}
          {data && (
            <pre className="m-0 overflow-x-auto whitespace-pre bg-muted p-4 font-mono text-xs leading-normal text-foreground">
              {renderDiff(data.diff)}
            </pre>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function renderDiff(raw: string): React.ReactNode {
  if (!raw.trim()) return <span className="text-muted-foreground">Empty diff.</span>;
  return raw.split("\n").map((line, i) => {
    let tone = "text-foreground";
    if (line.startsWith("+") && !line.startsWith("+++")) tone = "text-status-complete";
    else if (line.startsWith("-") && !line.startsWith("---")) tone = "text-status-failed";
    else if (line.startsWith("@@")) tone = "text-status-running";
    else if (line.startsWith("diff ") || line.startsWith("index ")) tone = "text-muted-foreground";
    return (
      <span key={i} className={cn("block", tone)}>
        {line || " "}
      </span>
    );
  });
}
