import { useEffect, useState } from "react";
import { useFileContent, useFileHistory } from "../hooks/useFileContent";
import { errorCls } from "../lib/field";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "./ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

interface Props {
  runId: string;
  filePath: string | null;
  onClose: () => void;
}

export function FilePreviewPanel({ runId, filePath, onClose }: Props) {
  const [tab, setTab] = useState<"current" | "history">("current");
  const [selectedRef, setSelectedRef] = useState<string | null>(null);

  // Reset tab/ref when file changes
  useEffect(() => {
    setTab("current");
    setSelectedRef(null);
  }, [filePath]);

  const { data, loading, error } = useFileContent(runId, filePath, selectedRef);
  const { history, loading: histLoading } = useFileHistory(runId, tab === "history" ? filePath : null);

  if (!filePath) return null;

  const filename = filePath.split("/").pop() ?? filePath;

  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[560px] max-w-full gap-0 p-0">
        <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-4 pr-12">
          <div>
            <SheetTitle className="inline text-base">{filename}</SheetTitle>
            <SheetDescription className="ml-2 inline font-mono text-xs text-muted-foreground">{filePath}</SheetDescription>
          </div>
        </div>

        <Tabs value={tab} onValueChange={(v) => setTab(v as "current" | "history")} className="flex min-h-0 flex-1 flex-col">
          <TabsList className="mx-4 my-2 self-start">
            <TabsTrigger value="current">Current</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
          </TabsList>

          <div className="min-h-0 flex-1 overflow-y-auto">
            <TabsContent value="current" className="mt-0">
              {selectedRef && (
                <div className="flex items-center gap-2 border-b border-status-blocked/30 bg-status-blocked/10 px-4 py-1.5 text-xs text-muted-foreground">
                  Viewing at <code className="font-mono">{selectedRef.slice(0, 7)}</code>
                  <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]" onClick={() => setSelectedRef(null)}>
                    Back to current
                  </Button>
                </div>
              )}
              {loading && <div className="p-4 text-xs text-muted-foreground">Loading…</div>}
              {error && <div className={cn(errorCls, "m-4")}>{error}</div>}
              {data?.error && (
                <div className="p-4 text-xs text-muted-foreground">
                  {data.error === "binary or too large"
                    ? "This file is binary or too large to preview."
                    : data.error}
                </div>
              )}
              {data?.content !== undefined && (
                <pre className="m-0 overflow-x-auto whitespace-pre bg-muted p-4 font-mono text-xs leading-relaxed text-foreground">
                  {data.content}
                </pre>
              )}
            </TabsContent>

            <TabsContent value="history" className="mt-0 p-3">
              {histLoading && <div className="text-xs text-muted-foreground">Loading history…</div>}
              {!histLoading && history.length === 0 && (
                <div className="text-xs text-muted-foreground">No commits touching this file.</div>
              )}
              {history.map((entry) => (
                <Card
                  key={entry.sha}
                  className={cn(
                    "mb-2 cursor-pointer px-3 py-2",
                    selectedRef === entry.sha && "border-primary",
                  )}
                  onClick={() => {
                    setSelectedRef(entry.sha);
                    setTab("current");
                  }}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs font-semibold">{entry.subject || "(no message)"}</span>
                    <span className="font-mono text-xs text-muted-foreground">{entry.sha.slice(0, 7)}</span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {entry.author_role} · {new Date(entry.committed_at * 1000).toLocaleString()}
                  </div>
                </Card>
              ))}
            </TabsContent>
          </div>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
