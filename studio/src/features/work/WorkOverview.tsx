import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Alert, Textarea } from "../../components/ui/form-controls";
import { usePlan, useSendMessage, useSpec } from "../plan/api";
import type { BacklogItemWire, RunBranchWire } from "./api";
import { cardSubtext, laneItems, planQuestion, weekChanges } from "./overviewModel";
import { usePmName } from "../pm/api";

/** Sends a message to the project manager's plan (mode "update"), then opens the Plan tab. */
export function AskBox({
  projectId, title, hint, placeholder,
}: {
  projectId: string;
  title?: string;
  hint: string;
  placeholder?: string;
}) {
  const send = useSendMessage(projectId);
  const navigate = useNavigate();
  const [text, setText] = useState("");
  function submit() {
    const t = text.trim();
    if (!t) return;
    send.mutate({ text: t, mode: "update" }, {
      onSuccess: () => {
        setText("");
        navigate({ search: "?tab=plan" });
      },
    });
  }
  return (
    <Card className="p-3" data-testid="ask-box">
      {title && <h3 className="m-0 mb-1 text-sm font-semibold">{title}</h3>}
      <p className="m-0 mb-2 text-xs text-muted-foreground">{hint}</p>
      <Textarea
        aria-label="Message the plan"
        rows={2}
        placeholder={placeholder}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="mt-2 flex items-center gap-2">
        <Button size="sm" onClick={submit} disabled={send.isPending || !text.trim()}>
          {send.isPending ? "Sending…" : "Send to the plan"}
        </Button>
        {send.error && <span className="text-xs text-status-failed">{send.error.message}</span>}
      </div>
    </Card>
  );
}

function Lane({ title, items, empty, more }: { title: string; items: BacklogItemWire[]; empty: string; more?: number }) {
  return (
    <section aria-label={title} className="min-w-0" data-testid={`lane-${title}`}>
      <h3 className="m-0 mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title} <span className="font-normal">({items.length + (more ?? 0)})</span>
      </h3>
      {items.length === 0 ? (
        <p className="m-0 text-xs text-muted-foreground">{empty}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {items.map((i) => (
            <li key={i.id} className="rounded-md border border-border bg-card p-2.5 text-card-foreground">
              <div className="text-sm font-medium">{i.title}</div>
              <div className="mt-0.5 text-xs text-muted-foreground">{cardSubtext(i)}</div>
            </li>
          ))}
        </ul>
      )}
      {more ? <p className="m-0 mt-1 text-xs text-muted-foreground">+{more} more</p> : null}
    </section>
  );
}

/** The Work tab at Overview level (storyboard screen 3): an ask box, four lanes and what needs you. */
export function WorkOverview({
  projectId, items, pendingRuns, renderReview, notice,
}: {
  projectId: string;
  items: readonly BacklogItemWire[];
  pendingRuns: readonly RunBranchWire[];
  /** The review panel of one waiting session. */
  renderReview: (runId: string) => ReactNode;
  notice?: ReactNode;
}) {
  const plan = usePlan(projectId);
  const spec = useSpec(projectId);
  const pmName = usePmName();
  const [reviewing, setReviewing] = useState<string | null>(null);
  const lanes = laneItems(items);
  const question = planQuestion(plan.data);
  const draft = plan.data?.draft?.status === "open" ? plan.data.draft : null;
  const cosigns = pendingRuns.filter((r) => r.needs_cosign && !r.cosign);
  const changes = weekChanges(spec.data);
  const nothing = pendingRuns.length === 0 && !draft && !question && cosigns.length === 0;

  return (
    <div className="flex flex-col gap-4" data-testid="work-overview">
      {notice}
      <AskBox
        projectId={projectId}
        hint="Ask for something or report a problem. It goes to the plan as a draft you approve."
        placeholder="What should the team do next?"
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Lane title="Up next" items={lanes.upNext} empty="Nothing queued." />
          <Lane title="In a session" items={lanes.inSession} empty="No session is running." />
          <Lane title="Waiting for you" items={lanes.waiting} empty="Nothing to review." />
          <Lane title="Done" items={lanes.done} more={lanes.moreDone} empty="Nothing finished yet." />
        </div>
        <aside className="flex flex-col gap-4">
          <Card className="p-3" data-testid="needs-you">
            <h3 className="m-0 mb-2 text-sm font-semibold">Needs you</h3>
            {nothing && <p className="m-0 text-xs text-muted-foreground">Nothing is waiting for you.</p>}
            <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
              {pendingRuns.map((r) => (
                <li key={r.run_id}>
                  <div>A session is ready for review.</div>
                  <Button size="sm" variant="outline" className="mt-1"
                    onClick={() => setReviewing(reviewing === r.run_id ? null : r.run_id)}>
                    {reviewing === r.run_id ? "Hide" : "Review and accept"}
                  </Button>
                  {reviewing === r.run_id && <div className="mt-2">{renderReview(r.run_id)}</div>}
                </li>
              ))}
              {draft && (
                <li>
                  The plan has a draft waiting for approval.{" "}
                  <Link to="?tab=plan">Open the plan</Link>
                </li>
              )}
              {question && (
                <li>
                  {pmName} has a question: {question}{" "}
                  <Link to="?tab=plan">Answer in the plan</Link>
                </li>
              )}
              {cosigns.length > 0 && (
                <li>
                  {cosigns.length === 1 ? "A session needs" : `${cosigns.length} sessions need`} a co-sign from the reviewer before it can be accepted.
                </li>
              )}
            </ul>
          </Card>
          <Card className="p-3" data-testid="spec-changes">
            <h3 className="m-0 mb-2 text-sm font-semibold">Spec changes this week</h3>
            {changes.length === 0 ? (
              <p className="m-0 text-xs text-muted-foreground">The spec has not changed this week.</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs">
                {changes.map((c, i) => <li key={i}>{c.text}</li>)}
              </ul>
            )}
          </Card>
        </aside>
      </div>
    </div>
  );
}

export function WorkNotice({ children }: { children: ReactNode }) {
  return <Alert variant="info">{children}</Alert>;
}
