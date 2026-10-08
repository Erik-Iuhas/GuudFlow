import { useState } from "react";
import { Alert, EmptyState, SectionTitle, Table, Td, Th } from "../../components/ui/form-controls";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import {
  useDecideDesignDraft,
  useDecideNote,
  useDesign,
  useKnowledge,
  useRebuildFacts,
  type DesignDraftWire,
  type DesignWire,
  type KnowledgeNoteWire,
} from "./api";
import { changeLabel } from "./model";

/**
 * The project's design and knowledge base (role contracts plan §4.2, §4.3): what the tech
 * lead owns. Open design drafts wait here for the Lead seat; notes an agent proposed wait for
 * a person.
 */
export function DesignTab({ projectId }: { projectId: string }) {
  const design = useDesign(projectId);
  if (design.isLoading) return <p className="text-sm text-muted-foreground">Loading the design…</p>;
  if (design.error) return <Alert variant="error">{design.error.message}</Alert>;
  const data = design.data;
  if (!data) return null;
  const open = data.drafts.filter((d) => d.status === "open");
  return (
    <div className="flex flex-col gap-6" data-testid="design-tab">
      {open.map((d) => <DraftCard key={d.id} projectId={projectId} draft={d} />)}
      {data.design.empty ? (
        <EmptyState title="No design yet">
          The tech lead writes it at the start of the first session. In adopt mode it recovers the
          design from the existing code before anything changes.
        </EmptyState>
      ) : (
        <DesignBody design={data.design} />
      )}
      <KnowledgeSection projectId={projectId} />
    </div>
  );
}

function DraftCard({ projectId, draft }: { projectId: string; draft: DesignDraftWire }) {
  const decide = useDecideDesignDraft(projectId);
  const [left, setLeft] = useState<string[]>([]);
  const groups = draft.turn.groups;
  return (
    <Card className="p-4" data-testid={`design-draft-${draft.id}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge variant="waiting">waiting for the Lead seat</Badge>
        <span className="text-sm font-semibold">Design draft {draft.id}</span>
        <span className="text-xs text-muted-foreground">
          from {draft.by}{draft.run_id ? ` in ${draft.run_id}` : ""}
        </span>
      </div>
      {decide.error && <Alert variant="error">{decide.error.message}</Alert>}
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {groups.map((g) => {
          const changes = draft.turn.changes.filter((c) => c.group === g.name);
          const kept = !left.includes(g.name);
          return (
            <li key={g.name} className="rounded-md border border-border p-3">
              <label className="flex items-start gap-2">
                <input type="checkbox" checked={kept} aria-label={`Include ${g.name}`}
                  onChange={() => setLeft(kept ? [...left, g.name] : left.filter((x) => x !== g.name))} />
                <span>
                  <span className="text-sm font-semibold">{g.name}</span>
                  <span className="block text-sm">{g.summary}</span>
                  <span className="block text-xs text-muted-foreground">Because {g.because.charAt(0).toLowerCase() + g.because.slice(1)}</span>
                </span>
              </label>
              <ul className="mb-0 mt-2 pl-8 text-sm">
                {changes.map((c, i) => <li key={i}>{changeLabel(c)}</li>)}
              </ul>
            </li>
          );
        })}
      </ul>
      <div className="mt-3 flex gap-2">
        <Button size="sm" disabled={decide.isPending || left.length === groups.length}
          onClick={() => decide.mutate({ draftId: draft.id, approve: true, rejectGroups: left })}>
          {left.length ? `Approve ${groups.length - left.length} of ${groups.length}` : "Approve"}
        </Button>
        <Button size="sm" variant="outline" disabled={decide.isPending}
          onClick={() => decide.mutate({ draftId: draft.id, approve: false })}>
          Discard
        </Button>
      </div>
    </Card>
  );
}

function active<T extends { status: string }>(rows: T[]): T[] {
  return rows.filter((r) => r.status !== "dropped" && r.status !== "superseded");
}

function DesignBody({ design }: { design: DesignWire }) {
  const components = active(design.components);
  const names = Object.fromEntries(design.components.map((c) => [c.id, c.name]));
  const who = (id: string) => (names[id] ? `${id} ${names[id]}` : id);
  const debt = design.debt.filter((d) => d.status === "open");
  return (
    <>
      <div>
        <SectionTitle className="mt-0">Components</SectionTitle>
        <Table>
          <thead><tr><Th>Id</Th><Th>Component</Th><Th>Owns</Th><Th>Responsibility</Th></tr></thead>
          <tbody>
            {components.map((c) => (
              <tr key={c.id}>
                <Td className="font-mono text-xs">{c.id}</Td>
                <Td>{c.name}</Td>
                <Td className="font-mono text-xs">{c.paths.join(", ")}</Td>
                <Td>{c.responsibility}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
      {active(design.interfaces).length > 0 && (
        <div>
          <SectionTitle>Interfaces</SectionTitle>
          <Table>
            <thead><tr><Th>Id</Th><Th>Signature</Th><Th>Provided by</Th><Th>Used by</Th></tr></thead>
            <tbody>
              {active(design.interfaces).map((i) => (
                <tr key={i.id}>
                  <Td className="font-mono text-xs">{i.id}</Td>
                  <Td><code className="text-xs">{i.signature}</code>
                    {i.description && <span className="block text-xs text-muted-foreground">{i.description}</span>}
                  </Td>
                  <Td>{who(i.provider)}</Td>
                  <Td>{(i.users ?? []).map(who).join(", ") || "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      {active(design.datatypes).length > 0 && (
        <div>
          <SectionTitle>Data</SectionTitle>
          <div className="grid gap-3 md:grid-cols-2">
            {active(design.datatypes).map((t) => (
              <Card key={t.id} className="p-3">
                <p className="m-0 text-sm font-semibold"><span className="font-mono text-xs">{t.id}</span> {t.name}</p>
                {t.description && <p className="m-0 text-xs text-muted-foreground">{t.description}</p>}
                <ul className="mb-0 mt-2 pl-4 text-xs">
                  {t.fields.map((f) => (
                    <li key={f.name}><code>{f.name}: {f.type}</code>{f.meaning ? ` — ${f.meaning}` : ""}</li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        </div>
      )}
      {active(design.flows).length > 0 && (
        <div>
          <SectionTitle>Flows</SectionTitle>
          {active(design.flows).map((f) => (
            <details key={f.id} className="mb-2 rounded-md border border-border p-2">
              <summary className="cursor-pointer text-sm">
                <span className="font-mono text-xs">{f.id}</span> {f.title}
                <span className="ml-2 text-xs text-muted-foreground">{f.kind} diagram (Mermaid)</span>
              </summary>
              <pre className="mt-2 overflow-x-auto text-xs">{f.diagram}</pre>
            </details>
          ))}
        </div>
      )}
      {design.decisions.length > 0 && (
        <div>
          <SectionTitle>Technical decisions</SectionTitle>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {design.decisions.map((d) => (
              <li key={d.id} className={d.status === "superseded" ? "opacity-60" : ""}>
                <span className="font-mono text-xs">{d.id}</span>{" "}
                <span className="text-sm font-semibold">{d.title}</span>{" "}
                {d.binding && <Badge variant="outline">binding</Badge>}{" "}
                {d.status === "superseded" && <Badge variant="secondary">superseded</Badge>}
                <span className="block text-sm">{d.choice}</span>
                <span className="block text-xs text-muted-foreground">{d.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {debt.length > 0 && (
        <div>
          <SectionTitle>Known debt</SectionTitle>
          <ul className="m-0 pl-4 text-sm">
            {debt.map((d) => (
              <li key={d.id}>
                <span className="font-mono text-xs">{d.id}</span> {d.text}
                {(d.components ?? []).length > 0 && (
                  <span className="text-xs text-muted-foreground"> ({(d.components ?? []).map(who).join(", ")})</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

const NOTE_ORDER: KnowledgeNoteWire["status"][] = ["proposed", "stale", "approved"];

function KnowledgeSection({ projectId }: { projectId: string }) {
  const knowledge = useKnowledge(projectId);
  const decide = useDecideNote(projectId);
  const rebuild = useRebuildFacts(projectId);
  const facts = knowledge.data?.facts ?? null;
  const notes = (knowledge.data?.notes ?? []).filter((n) => n.status !== "dropped");
  notes.sort((a, b) => NOTE_ORDER.indexOf(a.status) - NOTE_ORDER.indexOf(b.status));
  return (
    <div data-testid="knowledge">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Knowledge base</SectionTitle>
        <Button size="sm" variant="outline" disabled={rebuild.isPending} onClick={() => rebuild.mutate()}>
          {rebuild.isPending ? "Rebuilding…" : "Rebuild facts from main"}
        </Button>
      </div>
      {(knowledge.error ?? rebuild.error ?? decide.error) && (
        <Alert variant="error">{(knowledge.error ?? rebuild.error ?? decide.error)?.message}</Alert>
      )}
      {facts ? (
        <p className="mt-0 text-sm text-muted-foreground">
          Generated from <code>{facts.commit.slice(0, 8)}</code>: {facts.modules.length} modules,{" "}
          {facts.tests.length} test files, {Object.keys(facts.test_map).length} checks with a test that
          verified them{facts.commands.length ? <>; check command <code>{facts.commands[0]}</code></> : null}.
        </p>
      ) : (
        <p className="mt-0 text-sm text-muted-foreground">No facts yet: they are generated from main after the first accepted session.</p>
      )}
      {facts && facts.modules.length > 0 && (
        <details className="mb-3">
          <summary className="cursor-pointer text-sm">Modules on main</summary>
          <Table>
            <thead><tr><Th>File</Th><Th>Lines</Th><Th>Component</Th><Th>Exports</Th></tr></thead>
            <tbody>
              {facts.modules.slice(0, 200).map((m) => (
                <tr key={m.path}>
                  <Td className="font-mono text-xs">{m.path}</Td>
                  <Td>{m.lines}</Td>
                  <Td className="font-mono text-xs">{m.component ?? "—"}</Td>
                  <Td className="text-xs">{m.exports.slice(0, 6).join(", ")}{m.exports.length > 6 ? " …" : ""}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </details>
      )}
      {notes.length === 0 ? (
        <p className="text-sm text-muted-foreground">No notes yet. The tech lead promotes what more than one role needs from the session notebooks.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {notes.map((n) => (
            <li key={n.id} className="rounded-md border border-border p-2" data-testid={`note-${n.id}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs">{n.id}</span>
                <span className="text-sm font-semibold">{n.topic}</span>
                <Badge variant={n.status === "approved" ? "complete" : n.status === "stale" ? "blocked" : "waiting"}>
                  {n.status === "approved" ? `approved by ${n.approved_by === "human" ? "you" : n.approved_by}` : n.status}
                </Badge>
              </div>
              <p className="my-1 text-sm">{n.text}</p>
              {n.paths.length > 0 && <p className="m-0 font-mono text-xs text-muted-foreground">{n.paths.join(", ")}</p>}
              {n.status === "stale" && n.stale_since && (
                <p className="m-0 text-xs text-muted-foreground">Its files changed in {n.stale_since}; confirm it still holds.</p>
              )}
              {(n.status !== "approved" || n.approved_by !== "human") && (
                <div className="mt-2 flex gap-2">
                  <Button size="sm" variant="outline" disabled={decide.isPending}
                    onClick={() => decide.mutate({ noteId: n.id, approve: true })}>
                    {n.status === "approved" ? "Confirm" : "Approve"}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={decide.isPending}
                    onClick={() => decide.mutate({ noteId: n.id, approve: false })}>
                    Drop
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
