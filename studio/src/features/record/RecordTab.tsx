import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { projectsApi } from "../../api";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Alert, Input, SectionTitle, linkClass } from "../../components/ui/form-controls";
import { Segmented } from "../../components/ui/segmented";
import { useSpec } from "../plan/api";
import { recordApi, useSpecFiles, useTrace, type ExportFormat, type ExportSection, type RecordEventKind } from "./api";
import { KIND_LABEL, eventTime, linkify, versionCount } from "./model";

const KIND_VARIANT: Record<RecordEventKind, BadgeProps["variant"]> = {
  spec: "running",
  decision: "complete",
  plan: "outline",
  work: "secondary",
  session: "waiting",
  review: "blocked",
  status: "default",
};

const SECTIONS: { key: ExportSection; label: string; on: boolean }[] = [
  { key: "spec", label: "Spec with history", on: true },
  { key: "decisions", label: "Decisions", on: true },
  { key: "sessions", label: "Sessions and reviews", on: true },
  { key: "plan", label: "Plan conversations", on: false },
  { key: "rules", label: "Rule issues", on: false },
  { key: "costs", label: "Costs", on: false },
];

function Trace({ projectId, refId, onTrace }: { projectId: string; refId: string; onTrace: (ref: string) => void }) {
  const trace = useTrace(projectId, refId);
  const spec = useSpec(projectId);
  if (trace.isLoading) return <p className="m-0 text-sm text-muted-foreground">Tracing {refId}…</p>;
  if (trace.error) return <Alert variant="error">{trace.error.message}</Alert>;
  if (!trace.data) return null;
  const events = trace.data.events;
  const found =
    spec.data?.requirements.find((r) => r.id === refId)?.text ?? spec.data?.decisions.find((d) => d.id === refId)?.title;
  return (
    <div className="flex flex-col gap-3" data-testid="trace">
      <div>
        <h3 className="m-0 text-base font-semibold">
          {refId}
          {found ? ` ${found}` : ""}
        </h3>
        <p className="m-0 text-sm text-muted-foreground">
          {versionCount(events)} · how it changed, who changed it, what it touched
        </p>
      </div>
      <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label={`Timeline of ${refId}`}>
        {events.map((e, i) => (
          <li key={`${e.at}-${i}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm" data-testid="event">
            <time className="font-mono text-xs tabular-nums text-muted-foreground">{eventTime(e.at)}</time>
            <Badge variant={KIND_VARIANT[e.kind]}>{KIND_LABEL[e.kind]}</Badge>
            <span className="min-w-0 flex-1 basis-64 break-words">
              {linkify(e.text, e.links).map((p, n) =>
                p.ref ? (
                  <button
                    key={n}
                    type="button"
                    className={`${linkClass} cursor-pointer border-0 bg-transparent p-0 font-mono text-sm`}
                    onClick={() => onTrace(p.ref as string)}
                  >
                    {p.text}
                  </button>
                ) : (
                  <span key={n}>{p.text}</span>
                ),
              )}
              {e.by && e.kind !== "status" && <span className="text-muted-foreground"> · {e.by}</span>}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function ExportPanel({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [on, setOn] = useState<Record<ExportSection, boolean>>(
    () => Object.fromEntries(SECTIONS.map((s) => [s.key, s.on])) as Record<ExportSection, boolean>,
  );
  const [fmt, setFmt] = useState<ExportFormat>("md");
  const [error, setError] = useState<string | null>(null);
  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => projectsApi.get(projectId) });
  const [keep, setKeep] = useState(false);
  useEffect(() => {
    if (project.data) setKeep(project.data.record?.keep_in_repo === true);
  }, [project.data]);
  const files = useSpecFiles(projectId);

  const save = useMutation({
    mutationFn: async (keepInRepo: boolean) => {
      const latest = await projectsApi.get(projectId);
      return projectsApi.update(projectId, { ...latest, record: { ...latest.record, keep_in_repo: keepInRepo } });
    },
    onSuccess: (p) => qc.setQueryData(["project", projectId], p),
    onError: () => setKeep(project.data?.record?.keep_in_repo === true),
  });

  const download = async () => {
    setError(null);
    try {
      const { blob, filename } = await recordApi.download(
        projectId,
        SECTIONS.filter((s) => on[s.key]).map((s) => s.key),
        fmt,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The export failed");
    }
  };
  const nothing = !SECTIONS.some((s) => on[s.key]);

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-3 p-4">
        <SectionTitle className="m-0">Export the record</SectionTitle>
        <div className="flex flex-col gap-1.5">
          {SECTIONS.map((s) => (
            <label key={s.key} className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={on[s.key]}
                onChange={(e) => setOn({ ...on, [s.key]: e.target.checked })}
              />
              {s.label}
            </label>
          ))}
        </div>
        <Segmented<ExportFormat>
          label="Export format"
          value={fmt}
          onChange={setFmt}
          options={[
            { value: "md", label: "Markdown" },
            { value: "json", label: "JSON" },
          ]}
        />
        <div>
          <Button onClick={() => void download()} disabled={nothing}>
            Download
          </Button>
        </div>
        {error && <Alert variant="error">{error}</Alert>}
        <label className="flex cursor-pointer items-start gap-2 border-t border-border pt-3 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={keep}
            disabled={project.isLoading || save.isPending}
            onChange={(e) => {
              setKeep(e.target.checked);
              save.mutate(e.target.checked);
            }}
          />
          <span>
            Keep <code>SPEC.md</code> and <code>docs/decisions/</code> in the project repo, updated on each accepted
            session
          </span>
        </label>
        {save.error && <Alert variant="error">{save.error.message}</Alert>}
      </Card>
      <div>
        <h4 className="m-0 mb-1 text-sm font-medium text-muted-foreground">
          SPEC.md preview
          {files.data && Object.keys(files.data.files).length > 1
            ? ` (+ ${Object.keys(files.data.files).length - 1} decision file${Object.keys(files.data.files).length > 2 ? "s" : ""})`
            : ""}
        </h4>
        {files.isLoading ? (
          <p className="m-0 text-sm text-muted-foreground">Loading…</p>
        ) : files.error ? (
          <Alert variant="error">{files.error.message}</Alert>
        ) : (
          <pre
            aria-label="SPEC.md preview"
            className="m-0 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-muted p-3 font-mono text-xs"
          >
            {files.data?.files["SPEC.md"] ?? ""}
          </pre>
        )}
      </div>
    </div>
  );
}

/** The Record tab: trace an id through the project's history, export it, keep it in the repo. */
export function RecordTab({ projectId }: { projectId: string }) {
  const [draft, setDraft] = useState("");
  const [ref, setRef] = useState("");
  const go = (next: string) => {
    const clean = next.trim();
    setDraft(clean);
    setRef(clean);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    go(draft);
  };
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <section aria-label="Trace" className="flex min-w-0 flex-col gap-4">
        <form onSubmit={submit} className="flex gap-2">
          <Input
            aria-label="Trace"
            placeholder="Trace R3, D4, B7, a session or a file"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Button type="submit" variant="outline" disabled={draft.trim() === ""}>
            Trace
          </Button>
        </form>
        {ref === "" ? (
          <p className="m-0 text-sm text-muted-foreground">
            Enter a requirement, decision, backlog item, session or file to see how it came to be.
          </p>
        ) : (
          <Trace projectId={projectId} refId={ref} onTrace={go} />
        )}
      </section>
      <aside aria-label="Export" className="min-w-0">
        <ExportPanel projectId={projectId} />
      </aside>
    </div>
  );
}
