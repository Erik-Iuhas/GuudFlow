import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { projectsApi } from "../../api";
import { Button } from "../../components/ui/button";
import { Markdown } from "../../lib/markdown";
import { cn } from "../../lib/utils";
import { clock, usePendingClock } from "../cockpit/receipts";
import { useApproveDraft, usePlan, usePlanPreview, useSendMessage, type OwnerContextWire, type PlanMode } from "../plan/api";
import { usePmName } from "../pm/api";
import { JackActions } from "./JackActions";
import { placeLabel, useJack, type JackPlace } from "./context";

// Jack on every page (docs/JACK_DOCK_PLAN.md): one thread per project, the same one as the
// Plan tab; the page the owner is on goes with each message as a chip they can remove.

const SHOWN = 8;
const SEEN_KEY = "guudflow.jack.seen";

function readSeen(): Record<string, number> {
  try {
    return JSON.parse(localStorage.getItem(SEEN_KEY) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

function writeSeen(projectId: string, id: number): void {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify({ ...readSeen(), [projectId]: id }));
  } catch {
    /* a private window: the dot just stays */
  }
}

/** The owner's place without the project id (what the API takes). */
function wireContext(p: JackPlace): OwnerContextWire {
  const { projectId: _ignored, ...rest } = p;
  return rest;
}

export function JackDock() {
  const { place, open, setOpen } = useJack();
  const [picked, setPicked] = useState<string | null>(null);
  const projectId = place.projectId ?? picked;
  const pmName = usePmName();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setOpen(!open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  return (
    <>
      {!open && (
        <div className="fixed bottom-4 right-4 z-40 flex items-center gap-2">
          {projectId && <Nudge projectId={projectId} />}
          <button
            type="button"
            aria-label={`Open ${pmName}`}
            aria-keyshortcuts="Control+J Meta+J"
            onClick={() => setOpen(true)}
            className="relative flex h-12 w-12 items-center justify-center rounded-full border-0 bg-primary text-lg font-semibold text-primary-foreground shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            data-testid="jack-launcher"
          >
            {pmName.charAt(0).toUpperCase()}
            {projectId && <Dot projectId={projectId} />}
          </button>
        </div>
      )}
      {open && (
        <section
          aria-label={`${pmName} chat`}
          data-testid="jack-dock"
          className="fixed inset-0 z-50 flex flex-col bg-card text-card-foreground sm:inset-auto sm:bottom-4 sm:right-4 sm:h-[min(72vh,640px)] sm:w-[380px] sm:rounded-xl sm:border sm:border-border sm:shadow-2xl"
        >
          <header className="flex items-center gap-2 border-b border-border px-3 py-2">
            <button type="button" className="h-9 w-9 rounded-md border-0 bg-transparent text-lg sm:hidden" aria-label="Back" onClick={() => setOpen(false)}>←</button>
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground" aria-hidden>
              {pmName.charAt(0).toUpperCase()}
            </span>
            <span className="text-sm font-semibold">{pmName}</span>
            {projectId && <span className="truncate text-xs text-muted-foreground">{projectId}</span>}
            {projectId && (
              <Link className="ml-auto text-xs" to={`/projects/${encodeURIComponent(projectId)}?tab=plan`} onClick={() => setOpen(false)}>
                Open in Plan
              </Link>
            )}
            <button type="button" aria-label="Close" onClick={() => setOpen(false)}
              className={cn("hidden h-8 w-8 rounded-md border-0 bg-transparent text-muted-foreground hover:bg-accent sm:block", !projectId && "ml-auto")}>
              ×
            </button>
          </header>
          {projectId ? (
            <Thread projectId={projectId} onSwitch={place.projectId ? undefined : () => setPicked(null)} />
          ) : (
            <ProjectPicker onPick={setPicked} />
          )}
        </section>
      )}
    </>
  );
}

/** The red dot: an unread reply or a draft waiting for the owner. */
function Dot({ projectId }: { projectId: string }) {
  const plan = usePlan(projectId).data;
  const lastPm = [...(plan?.messages ?? [])].reverse().find((m) => m.author === "pm");
  const unread = lastPm ? lastPm.id > (readSeen()[projectId] ?? 0) : false;
  const draft = (plan?.draft?.changes.length ?? 0) > 0;
  if (!unread && !draft) return null;
  return <span className="absolute right-0.5 top-0.5 h-3 w-3 rounded-full border-2 border-background bg-destructive" data-testid="jack-dot" />;
}

/** What waits, said in words next to the launcher. */
function Nudge({ projectId }: { projectId: string }) {
  const plan = usePlan(projectId).data;
  const n = plan?.draft?.changes.length ?? 0;
  if (!n) return null;
  return (
    <span className="hidden rounded-md bg-foreground px-2 py-1 text-xs text-background sm:inline" data-testid="jack-nudge">
      {n} change{n === 1 ? "" : "s"} drafted
    </span>
  );
}

function ProjectPicker({ onPick }: { onPick: (id: string) => void }) {
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => projectsApi.list() });
  const [idea, setIdea] = useState("");
  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-3 text-sm" data-testid="jack-picker">
      <p className="m-0 text-muted-foreground">Which project do you want to talk about?</p>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {(projects.data ?? []).slice(0, 8).map((p) => (
          <li key={p.project_id}>
            <button type="button" onClick={() => onPick(p.project_id)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-left text-sm hover:bg-accent">
              {p.project_id}
            </button>
          </li>
        ))}
      </ul>
      <form className="flex flex-col gap-1.5 border-t border-border pt-3" onSubmit={(e) => e.preventDefault()}>
        <label htmlFor="jack-new" className="text-xs text-muted-foreground">Or start a new one: describe what you want to build</label>
        <input id="jack-new" value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="A small roguelike in Python…"
          className="h-9 rounded-md border border-input bg-background px-2 text-sm" />
        <Link to={`/projects/new${idea.trim() ? `?goal=${encodeURIComponent(idea.trim())}` : ""}`}
          className="self-start text-xs">Start a project →</Link>
      </form>
    </div>
  );
}

function Thread({ projectId, onSwitch }: { projectId: string; onSwitch?: () => void }) {
  const { place, draft: starter, clearDraft } = useJack();
  const pmName = usePmName();
  const plan = usePlan(projectId);
  const send = useSendMessage(projectId);
  const approve = useApproveDraft(projectId);
  const preview = usePlanPreview(projectId, send.isPending);
  const seconds = usePendingClock(send.isPending);
  const [text, setText] = useState("");
  const [mode, setMode] = useState<PlanMode>("discuss");
  const [withPlace, setWithPlace] = useState(true);
  const [outgoing, setOutgoing] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const messages = plan.data?.messages ?? [];
  const shown = messages.slice(-SHOWN);
  const lastPm = useMemo(() => [...messages].reverse().find((m) => m.author === "pm"), [messages]);
  const placeKey = JSON.stringify(place);

  useEffect(() => { if (starter) { setText(starter); clearDraft(); } }, [starter, clearDraft]);
  useEffect(() => setWithPlace(true), [placeKey]);
  useEffect(() => { if (lastPm) writeSeen(projectId, lastPm.id); }, [projectId, lastPm]);
  useEffect(() => { endRef.current?.scrollIntoView?.({ block: "end" }); }, [shown.length, outgoing]);

  const hasPlace = place.page !== "other" || !!place.run_id;
  const context = withPlace && hasPlace ? wireContext(place) : null;
  const draft = plan.data?.draft;
  const draftChanges = draft?.changes.length ?? 0;
  const small = draftChanges > 0 && draftChanges <= 5 && Object.keys(draft?.groups ?? {}).length <= 1;

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const t = text.trim();
    if (!t || send.isPending) return;
    setOutgoing(t);
    setText("");
    send.mutate({ text: t, mode, context }, {
      onError: () => setText(t),
      onSettled: () => setOutgoing(null),
    });
  }

  return (
    <>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto px-3 py-3 text-sm" data-testid="jack-thread">
        {onSwitch && (
          <button type="button" onClick={onSwitch} className="self-start border-0 bg-transparent p-0 text-xs text-muted-foreground underline">
            ← other projects
          </button>
        )}
        {messages.length > SHOWN && (
          <Link to={`/projects/${encodeURIComponent(projectId)}?tab=plan`} className="self-center text-xs">
            Earlier messages in Plan
          </Link>
        )}
        {shown.map((m) => m.author === "human" ? (
          <div key={m.id} className="max-w-[85%] self-end rounded-lg bg-primary px-2.5 py-1.5 text-primary-foreground">
            <p className="m-0 whitespace-pre-wrap break-words">{m.text}</p>
            {m.context?.label && <p className="m-0 mt-0.5 text-[11px] opacity-80">from {m.context.label}</p>}
          </div>
        ) : (
          <div key={m.id} className="max-w-[92%] self-start rounded-lg bg-muted px-2.5 py-1.5">
            <Markdown text={m.text} className="break-words text-sm" />
            <JackActions projectId={projectId} actions={m.actions ?? []} />
          </div>
        ))}
        {outgoing && (
          <div className="max-w-[85%] self-end rounded-lg bg-primary/80 px-2.5 py-1.5 text-primary-foreground" data-testid="jack-outgoing">
            <p className="m-0 whitespace-pre-wrap break-words">{outgoing}</p>
            <p className="m-0 mt-0.5 text-[11px] opacity-80">
              Sent · {pmName} is {mode === "update" ? "drafting" : "looking"}
              {(preview.data?.changes.length ?? 0) > 0 ? ` · ${preview.data?.changes.length} change(s) so far` : ""}
              {" "}<span className="tabular-nums">{clock(seconds)}</span>
            </p>
          </div>
        )}
        {send.error && <p role="alert" className="m-0 text-xs text-destructive">{send.error.message}</p>}
        <div ref={endRef} />
      </div>
      {draftChanges > 0 && (
        <div className="mx-3 mb-2 flex flex-col gap-1.5 rounded-lg border border-border p-2 text-xs" data-testid="jack-draft">
          <span className="font-semibold text-muted-foreground">Draft · {draftChanges} change{draftChanges === 1 ? "" : "s"}</span>
          {approve.error && <span role="alert" className="text-destructive">{approve.error.message}</span>}
          <div className="flex gap-1.5">
            {small && (
              <Button size="sm" className="h-7 px-2 text-xs" disabled={approve.isPending} onClick={() => approve.mutate()}>
                {approve.isPending ? "Approving…" : "Approve now"}
              </Button>
            )}
            <Button asChild size="sm" variant="outline" className="h-7 px-2 text-xs">
              <Link to={`/projects/${encodeURIComponent(projectId)}?tab=plan`}>Review in Plan →</Link>
            </Button>
          </div>
        </div>
      )}
      <form onSubmit={submit} className="flex flex-col gap-2 border-t border-border px-3 py-2">
        {hasPlace && withPlace && (
          <span className="flex items-center gap-1 self-start rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary" data-testid="jack-chip">
            Looking at: {placeLabel(place)}
            <button type="button" aria-label="Don't send where I am" onClick={() => setWithPlace(false)}
              className="border-0 bg-transparent p-0 text-xs text-primary">×</button>
          </span>
        )}
        <label className="sr-only" htmlFor="jack-input">Message {pmName}</label>
        <textarea id="jack-input" value={text} onChange={(e) => setText(e.target.value)} rows={2}
          placeholder={mode === "discuss" ? `Ask ${pmName} about this…` : "What should change in the plan?"}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
          className="min-h-[2.5rem] resize-none rounded-md border border-input bg-background px-2 py-1.5 text-sm" />
        <div className="flex items-center gap-1 text-xs">
          <div role="group" aria-label="Mode" className="inline-flex overflow-hidden rounded-md border border-input">
            {(["discuss", "update"] as const).map((m) => (
              <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}
                className={cn("border-0 px-2 py-1", mode === m ? "bg-foreground text-background" : "bg-transparent text-muted-foreground")}>
                {m === "discuss" ? "Ask" : "Change the plan"}
              </button>
            ))}
          </div>
          <Button type="submit" size="sm" className="ml-auto h-7 px-3 text-xs" disabled={!text.trim() || send.isPending}>Send</Button>
        </div>
      </form>
    </>
  );
}
