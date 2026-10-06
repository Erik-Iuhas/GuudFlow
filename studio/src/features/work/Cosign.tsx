import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Alert, Input, Select, Textarea } from "../../components/ui/form-controls";
import { useCosign, type CommentSeverity, type CosignComment } from "../spec/api";
import { workApi, workKeys, type RunBranchWire } from "./api";

const SEVERITIES: { value: CommentSeverity; label: string }[] = [
  { value: "note", label: "Note" },
  { value: "should_fix", label: "Should fix" },
  { value: "blocking", label: "Blocking" },
];

/** True when the owner may accept: no co-sign is required, or it was given. */
export function canAccept(run: Pick<RunBranchWire, "needs_cosign" | "cosign">): boolean {
  return !run.needs_cosign || run.cosign?.verdict === "cosign";
}

export const COSIGN_WHY = "Co-sign the code first: the reviewer has to sign off before this reaches the project.";

function where(c: { file?: string; line?: number }): string {
  return c.file ? `${c.file}${c.line ? `:${c.line}` : ""} ` : "";
}

/** The reviewer's co-sign area on a waiting session: comments, then co-sign or request changes. */
export function Cosign({
  projectId, run, onViewChanges,
}: {
  projectId: string;
  run: RunBranchWire;
  onViewChanges: () => void;
}) {
  const cosign = useCosign(projectId);
  const qc = useQueryClient();
  const [comments, setComments] = useState<CosignComment[]>([]);
  const [file, setFile] = useState("");
  const [line, setLine] = useState("");
  const [text, setText] = useState("");
  const [severity, setSeverity] = useState<CommentSeverity>("note");
  const [error, setError] = useState<string | null>(null);

  function addComment() {
    if (!text.trim()) return;
    const c: CosignComment = { text: text.trim(), severity };
    if (file.trim()) c.file = file.trim();
    if (line.trim() && Number.isFinite(Number(line))) c.line = Number(line);
    setComments((l) => [...l, c]);
    setText("");
    setLine("");
  }

  function submit(verdict: "cosign" | "changes", followUp = false) {
    setError(null);
    cosign.mutate({ runId: run.run_id, verdict, comments }, {
      onSuccess: () => {
        if (!followUp) return;
        void workApi.add(projectId, {
          title: `Follow-up from the review of ${run.run_id}`,
          acceptance: comments.map((c) => `${where(c)}${c.text}`),
          kind: "code",
          size: "S",
        }).then(() => qc.invalidateQueries({ queryKey: workKeys.backlog(projectId) }))
          .catch((e: unknown) => setError(`The co-sign was saved, but the follow-up item failed: ${String(e)}`));
      },
      onError: (e) => setError(e.message),
    });
  }

  const given = run.cosign;
  return (
    <section className="mt-2 rounded-md border border-border p-2" aria-label="Co-sign" data-testid={`cosign-${run.run_id}`}>
      <h4 className="m-0 mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Co-sign</h4>
      {given && (
        <p className="m-0 mb-1 flex flex-wrap items-center gap-2 text-sm">
          <Badge variant={given.verdict === "cosign" ? "complete" : "failed"}>
            {given.verdict === "cosign" ? "co-signed" : "changes requested"}
          </Badge>
          <span className="text-muted-foreground">by {given.by}</span>
        </p>
      )}
      {given && given.comments.length > 0 && (
        <ul className="m-0 mb-1 list-none p-0 text-xs">
          {given.comments.map((c, i) => (
            <li key={i}>[{c.severity}] {where(c)}{c.text}</li>
          ))}
        </ul>
      )}
      {!given && (
        <p className="m-0 mb-1 text-sm text-muted-foreground">
          This session needs a reviewer to read the code before it can be accepted.{" "}
          <button type="button" className="cursor-pointer border-0 bg-transparent p-0 text-primary underline" onClick={onViewChanges}>
            View the diff
          </button>
        </p>
      )}
      {comments.length > 0 && (
        <ul className="m-0 mb-1 list-none p-0 text-xs" aria-label="Your comments">
          {comments.map((c, i) => (
            <li key={i} className="flex items-center gap-2">
              <span>[{c.severity}] {where(c)}{c.text}</span>
              <button type="button" className="cursor-pointer border-0 bg-transparent p-0 text-muted-foreground underline"
                onClick={() => setComments((l) => l.filter((_, j) => j !== i))}>
                remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {!given && (
        <div className="grid gap-1.5">
          <div className="flex flex-wrap gap-1.5">
            <Input aria-label="Comment file" placeholder="file" className="w-40" value={file} onChange={(e) => setFile(e.target.value)} />
            <Input aria-label="Comment line" placeholder="line" className="w-20" value={line} onChange={(e) => setLine(e.target.value)} />
            <Select aria-label="Comment severity" className="w-32" value={severity} onChange={(e) => setSeverity(e.target.value as CommentSeverity)}>
              {SEVERITIES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </Select>
          </div>
          <Textarea aria-label="Comment text" rows={2} placeholder="What should change, or what is fine" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={addComment} disabled={!text.trim()}>Add comment</Button>
            <Button size="sm" onClick={() => submit("cosign")} disabled={cosign.isPending}>Co-sign</Button>
            <Button size="sm" variant="outline" onClick={() => submit("cosign", true)} disabled={cosign.isPending || comments.length === 0}>
              Co-sign, fix in a follow-up
            </Button>
            <Button size="sm" variant="outline" onClick={() => submit("changes")} disabled={cosign.isPending}>Request changes</Button>
          </div>
        </div>
      )}
      {error && <Alert variant="error" className="mt-2">{error}</Alert>}
    </section>
  );
}
