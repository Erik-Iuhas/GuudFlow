import { useEffect, useState, type FormEvent } from "react";
import { Alert, SectionTitle } from "../../components/ui/form-controls";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import {
  preflightApi,
  usePreflight,
  usePreflightAction,
  type AssumptionWire,
  type PlannedCardWire,
  type PreflightWire,
} from "./api";

const FIELD =
  "w-full rounded-md border border-input bg-background px-2 py-1 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * The session prepared with the tech lead before it starts (worker context plan H2): talk to
 * it, edit the cards, answer its questions, approve. Start then runs exactly these cards.
 */
export function PreflightPanel({ projectId }: { projectId: string }) {
  const query = usePreflight(projectId);
  const plan = query.data;
  if (query.isLoading || !plan) return null;
  return <PlanCard projectId={projectId} plan={plan} />;
}

function PlanCard({ projectId, plan }: { projectId: string; plan: PreflightWire }) {
  const approve = usePreflightAction(projectId, () => preflightApi.approve(projectId));
  const discard = usePreflightAction(projectId, () => preflightApi.discard(projectId));
  const error = approve.error ?? discard.error;
  const blocked = plan.open_questions.length > 0 || plan.problems.length > 0 || !plan.plan;
  return (
    <Card className="flex flex-col gap-4 p-4" data-testid="preflight">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">Next session, prepared with {plan.planner ?? "the tech lead"}</span>
        <Badge variant={plan.status === "approved" ? "complete" : "waiting"}>
          {plan.status === "approved" ? "approved" : "draft"}
        </Badge>
        <span className="text-xs text-muted-foreground">
          {plan.id}
          {plan.items.length ? ` · items ${plan.items.join(", ")}` : ""}
          {` · planning spend $${plan.spend_usd.toFixed(2)}`}
        </span>
      </div>
      {plan.status === "approved" && plan.stale.length > 0 && (
        <Alert variant="warn">
          This plan may be out of date: {plan.stale.join("; ")}. Ask the tech lead to refresh it below,
          or start anyway from the Work tab.
        </Alert>
      )}
      {plan.problems.length > 0 && (
        <Alert variant="error">The plan does not pass the planner's checks yet: {plan.problems.join("; ")}</Alert>
      )}
      {error && <Alert variant="error">{error.message}</Alert>}
      <Assumptions projectId={projectId} rows={plan.assumptions} />
      <Cards projectId={projectId} plan={plan} />
      <Conversation projectId={projectId} plan={plan} />
      <div className="flex flex-wrap items-center gap-2">
        {plan.status === "draft" ? (
          <Button size="sm" disabled={blocked || approve.isPending} onClick={() => approve.mutate(undefined)}
            title={plan.open_questions.length ? "Answer the owner's questions first" : undefined}>
            Approve the plan
          </Button>
        ) : (
          <span className="text-sm text-muted-foreground">Approved: start it from the Work tab.</span>
        )}
        <Button size="sm" variant="outline" disabled={discard.isPending} onClick={() => discard.mutate(undefined)}>
          Discard
        </Button>
        {plan.open_questions.length > 0 && (
          <span className="text-xs text-muted-foreground">
            Waiting on your answer: {plan.open_questions.join(", ")}
          </span>
        )}
      </div>
    </Card>
  );
}

function Assumptions({ projectId, rows }: { projectId: string; rows: AssumptionWire[] }) {
  if (rows.length === 0) return null;
  return (
    <section aria-label="Assumptions and questions" className="flex flex-col gap-2">
      <SectionTitle className="m-0">Assumptions and questions</SectionTitle>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {rows.map((a) => <AssumptionRow key={a.id} projectId={projectId} a={a} />)}
      </ul>
    </section>
  );
}

function AssumptionRow({ projectId, a }: { projectId: string; a: AssumptionWire }) {
  const answer = usePreflightAction(projectId, (text: string) => preflightApi.answer(projectId, a.id, text));
  const [text, setText] = useState("");
  const owner = a.decided_by === "owner";
  const [open, setOpen] = useState(owner && !a.answer);
  return (
    <li className="flex flex-col gap-1 rounded-md border border-border p-2 text-sm" data-testid={`assumption-${a.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">{a.id}</span>
        <Badge variant={owner ? (a.answer ? "complete" : "blocked") : "outline"}>
          {owner ? "you decide" : "tech lead decided"}
        </Badge>
        <span className="min-w-0 flex-1">{a.text}</span>
      </div>
      {a.choice && <span className="text-xs text-muted-foreground">{owner ? "Proposed: " : "Chose: "}{a.choice}</span>}
      {a.answer && <span className="text-xs">Your answer: <strong>{a.answer}</strong>{a.decision ? ` (decision ${a.decision})` : ""}</span>}
      {answer.error && <span role="alert" className="text-xs text-destructive">{answer.error.message}</span>}
      {open ? (
        <form className="flex gap-2" onSubmit={(e: FormEvent) => {
          e.preventDefault();
          if (!text.trim()) return;
          answer.mutate(text.trim(), { onSuccess: () => { setText(""); setOpen(false); } });
        }}>
          <input className={FIELD} aria-label={`Answer ${a.id}`} value={text} placeholder={a.choice || "Your answer"}
            onChange={(e) => setText(e.target.value)} />
          <Button size="sm" type="submit" disabled={answer.isPending}>Answer</Button>
        </form>
      ) : (
        <button type="button" className="self-start border-0 bg-transparent p-0 text-xs text-muted-foreground underline"
          onClick={() => setOpen(true)}>
          {owner ? "Change the answer" : "Override"}
        </button>
      )}
    </li>
  );
}

function Cards({ projectId, plan }: { projectId: string; plan: PreflightWire }) {
  const save = usePreflightAction(projectId, (tasks: PlannedCardWire[]) => preflightApi.cards(projectId, tasks));
  const tasks = plan.plan?.tasks ?? [];
  const [draft, setDraft] = useState<PlannedCardWire[] | null>(null);
  useEffect(() => setDraft(null), [plan]);
  const rows = draft ?? tasks;
  const owners = [...new Set(tasks.map((t) => t.owner_role))];
  const edit = (i: number, patch: Partial<PlannedCardWire>) =>
    setDraft(rows.map((t, n) => (n === i ? { ...t, ...patch } : t)));
  const nextId = () => `T${Math.max(0, ...rows.map((t) => parseInt(t.id.slice(1), 10) || 0)) + 1}`;
  return (
    <section aria-label="Session cards" className="flex flex-col gap-2">
      <SectionTitle className="m-0">Cards ({rows.length})</SectionTitle>
      {rows.length === 0 && <p className="m-0 text-sm text-muted-foreground">No cards yet.</p>}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {rows.map((t, i) => (
          <li key={`${t.id}-${i}`} className="grid gap-1 rounded-md border border-border p-2 text-sm" data-testid={`plan-card-${t.id}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted-foreground">{t.id}</span>
              <select className="rounded-md border border-input bg-background px-1 text-xs" aria-label={`Owner of ${t.id}`}
                value={t.owner_role} onChange={(e) => edit(i, { owner_role: e.target.value })}>
                {owners.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
              <Badge variant="outline">{t.kind}</Badge>
              <Badge variant="secondary">{t.size}</Badge>
              {t.depends_on?.length ? <span className="text-xs text-muted-foreground">after {t.depends_on.join(", ")}</span> : null}
              <button type="button" className="ml-auto border-0 bg-transparent text-xs text-muted-foreground underline"
                aria-label={`Remove ${t.id}`} onClick={() => setDraft(rows.filter((_, n) => n !== i))}>
                Remove
              </button>
            </div>
            <input className={FIELD} aria-label={`Title of ${t.id}`} value={t.title}
              onChange={(e) => edit(i, { title: e.target.value })} />
            <textarea className={`${FIELD} min-h-[3.5rem] text-xs`} aria-label={`Acceptance of ${t.id}`}
              value={t.acceptance.join("\n")}
              onChange={(e) => edit(i, { acceptance: e.target.value.split("\n") })} />
          </li>
        ))}
      </ul>
      {save.error && <Alert variant="error">{save.error.message}</Alert>}
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={() => setDraft([...rows, {
          id: nextId(), title: "", owner_role: owners[0] ?? "", acceptance: [""], size: "S", kind: "code",
        }])}>
          Add a card
        </Button>
        {draft && (
          <>
            <Button size="sm" disabled={save.isPending} onClick={() => save.mutate(rows.map((t) => ({
              ...t, acceptance: t.acceptance.map((a) => a.trim()).filter(Boolean),
            })))}>
              Save cards
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>Undo changes</Button>
          </>
        )}
      </div>
    </section>
  );
}

function Conversation({ projectId, plan }: { projectId: string; plan: PreflightWire }) {
  const send = usePreflightAction(projectId, (text: string) => preflightApi.message(projectId, text));
  const [text, setText] = useState("");
  const [outgoing, setOutgoing] = useState<string | null>(null);
  return (
    <section aria-label="Talk to the tech lead" className="flex flex-col gap-2">
      <SectionTitle className="m-0">Talk to {plan.planner ?? "the tech lead"}</SectionTitle>
      <ul className="m-0 flex max-h-64 list-none flex-col gap-1 overflow-y-auto p-0 text-sm">
        {plan.messages.map((m, i) => (
          <li key={i}><strong>{m.author === "owner" ? "You" : m.author}:</strong> {m.text}</li>
        ))}
        {outgoing && <li className="text-muted-foreground"><strong>You:</strong> {outgoing} (thinking…)</li>}
      </ul>
      {send.error && <Alert variant="error">{send.error.message}</Alert>}
      <form className="flex gap-2" onSubmit={(e: FormEvent) => {
        e.preventDefault();
        const t = text.trim();
        if (!t) return;
        setOutgoing(t);
        setText("");
        send.mutate(t, {
          onError: () => setText(t),
          onSettled: () => setOutgoing(null),
        });
      }}>
        <input className={FIELD} aria-label="Message to the tech lead" value={text}
          placeholder="e.g. use one module per level, split T3" onChange={(e) => setText(e.target.value)} />
        <Button size="sm" type="submit" disabled={send.isPending}>Send</Button>
      </form>
    </section>
  );
}
