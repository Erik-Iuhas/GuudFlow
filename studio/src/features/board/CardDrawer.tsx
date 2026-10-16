import { useEffect, useMemo, useState, type FormEvent } from "react";
import { cardsApi } from "../../api";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "../../components/ui/sheet";
import { cn } from "../../lib/utils";
import { useRunStore } from "../../stores/runStoreContext";
import {
  COLUMN_LABEL,
  THREAD_KIND_ICON,
  THREAD_KIND_LABEL,
  acceptanceState,
  mergeRanges,
  servedRequirements,
  splitCheckLine,
} from "./boardModel";
import { useCommentOnCard } from "./useCardMutations";
import { RoleChanges } from "../cockpit/RoleChanges";
import { DiffText } from "../report/DiffViewer";
import { OPEN_CODE_EVENT, openInCode } from "../code/api";
import { WhatItWasTold } from "../briefs/WhatItWasTold";
import type { SpecWire } from "../plan/api";
import type { CardView } from "../../stores/runEvents";

interface Props {
  runId: string;
  cardId: string | null;
  onClose: () => void;
  /** The project's spec, to name the requirements and checks a card serves. */
  spec?: SpecWire;
}

const SECTION = "grid gap-2";
const H = "m-0 text-sm font-semibold";

/** Side drawer for one card: acceptance checklist, thread, diffs, artifacts and a comment box. */
interface HandoffData {
  handoff?: { changed?: string; decisions?: string[]; tests?: string; next?: string };
  facts?: { files?: [string, number, number][]; exports?: string[] };
  source?: string;
}

/** A card's handoff (plan §11.1): what the next card reads instead of this card's conversation. */
function HandoffBody({ data }: { data: Record<string, unknown> | undefined }) {
  const d = (data ?? {}) as HandoffData;
  const h = d.handoff ?? {};
  const files = d.facts?.files ?? [];
  return (
    <div className="mt-1 grid gap-1 text-sm" data-testid="handoff">
      {d.source === "fallback" && <p className="m-0 text-xs text-muted-foreground">No handoff written; the result summary stands in.</p>}
      {h.changed && <p className="m-0"><span className="font-medium">Changed: </span>{h.changed}</p>}
      {(h.decisions?.length ?? 0) > 0 && (
        <div>
          <span className="font-medium">Decisions and gotchas</span>
          <ul className="m-0 list-disc pl-5">{h.decisions!.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
      {h.tests && <p className="m-0"><span className="font-medium">Tests: </span>{h.tests}</p>}
      {h.next && <p className="m-0"><span className="font-medium">For the next card: </span>{h.next}</p>}
      {files.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap gap-x-3 gap-y-0.5 p-0 text-xs text-muted-foreground" aria-label="Files">
          {files.map(([p, a, r]) => (
            <li key={p}>
              <button type="button" className="cursor-pointer bg-transparent p-0 font-mono text-xs text-primary underline-offset-2 hover:underline"
                      title="Open in the Code tab" onClick={() => openInCode({ path: p })}>
                {p}
              </button>{" "}
              <span className="text-status-complete">+{a}</span> <span className="text-status-failed">−{r}</span>
            </li>
          ))}
        </ul>
      )}
      {(d.facts?.exports?.length ?? 0) > 0 && (
        <ul className="m-0 list-none p-0 font-mono text-[11px] text-muted-foreground">
          {Object.entries(
            d.facts!.exports!.reduce<Record<string, string[]>>((acc, e) => {
              const [file, name] = e.split(": ", 2);
              (acc[file] ??= []).push(name ?? "");
              return acc;
            }, {}),
          ).map(([file, names]) => (
            <li key={file}><span className="text-foreground">{file}</span>: {names.join(", ")}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Why a card exists (plan §12): requirement → check → backlog item → card, with the spec's
 * words; or the cards it supports; or a plain note that it is not linked.
 */
type CheckState = "open" | "claimed" | "unmet" | "met";
const STATE_LABEL: Record<CheckState, string> = { open: "open", claimed: "claimed met", unmet: "not met", met: "met" };
const STATE_TONE: Record<CheckState, string> = {
  open: "bg-muted text-muted-foreground",
  claimed: "bg-status-waiting/15 text-status-waiting",
  unmet: "bg-status-failed/15 text-status-failed",
  met: "bg-status-complete/15 text-status-complete",
};

/** Where a spec check stands on this card (worker context plan D6): met once the card's review
 * passed, not met when the latest review flagged it, claimed when the worker said it was met. */
export function checkState(card: CardView, id: string): CheckState {
  if (card.column === "done") return "met";
  const thread = card.thread ?? [];
  const starts = (t: string) => t.replace(/^[✓✗]\s*/, "").toUpperCase().startsWith(`${id.toUpperCase()}:`);
  for (let i = thread.length - 1; i >= 0; i -= 1) {
    const e = thread[i];
    if (e.kind === "review_issue" && starts(e.text)) return "unmet";
    if (e.kind === "note" && starts(e.text)) return e.text.startsWith("✓") ? "claimed" : "unmet";
  }
  return "open";
}

export function WhyThisCard({ card, spec }: { card: CardView; spec?: SpecWire }) {
  const reqs = servedRequirements(card);
  const checks = (card.serves ?? []).filter((s) => s.includes("."));
  const chip = "rounded border px-1.5 font-mono text-xs";
  const req = (id: string) => spec?.requirements.find((r) => r.id === id);
  const checkText = (id: string) => req(id.split(".")[0])?.checks.find((c) => c.id === id)?.text;
  return (
    <section className="grid gap-1.5 rounded-r-md border-l-[3px] border-l-primary bg-primary/10 px-3 py-2" aria-label="Why this card">
      <h3 className="m-0 text-[11px] font-semibold uppercase tracking-wide text-primary">Why this card</h3>
      {reqs.length > 0 ? (
        reqs.map((rid) => {
          const r = req(rid);
          const mine = checks.filter((c) => c.startsWith(`${rid}.`));
          return (
            <div key={rid} className="grid gap-1 text-sm" data-testid={`why-${rid}`}>
              <p className="m-0 flex flex-wrap items-center gap-1.5">
                <span className={cn(chip, "border-primary/40 text-primary")}>{rid}</span>
                <span>{r?.title || r?.text || ""}</span>
                {card.backlogId && (
                  <>
                    <span aria-hidden className="text-muted-foreground">→</span>
                    <span className={cn(chip, "border-border text-muted-foreground")}>{card.backlogId}</span>
                  </>
                )}
                <span aria-hidden className="text-muted-foreground">→</span>
                <span className={cn(chip, "border-border")}>{card.id}</span>
              </p>
              {mine.map((c) => {
                const state = checkState(card, c);
                return (
                  <p key={c} className="m-0 pl-2 text-xs text-muted-foreground" data-testid={`check-${c}`}>
                    <span className="mr-1.5 font-mono text-foreground">{c}</span>
                    {checkText(c) ?? ""}
                    <span className={cn("ml-1.5 rounded px-1 text-[10px]", STATE_TONE[state])}>{STATE_LABEL[state]}</span>
                  </p>
                );
              })}
              {!mine.length && <p className="m-0 pl-2 text-xs text-muted-foreground">Works towards the whole requirement.</p>}
            </div>
          );
        })
      ) : (card.supports ?? []).length > 0 ? (
        <p className="m-0 text-sm">Support work for {(card.supports ?? []).join(", ")}: it serves no requirement itself.</p>
      ) : (
        <p className="m-0 text-sm text-muted-foreground">Not linked to the spec{card.backlogId ? ` (item ${card.backlogId})` : ""}.</p>
      )}
    </section>
  );
}

export function CardDrawer({ runId, cardId, onClose, spec }: Props) {
  const card = useRunStore((s) => (cardId ? s.cards[cardId] : undefined));
  // Opening a file in the Code tab closes the drawer so the tab is visible.
  useEffect(() => {
    if (!cardId) return;
    window.addEventListener(OPEN_CODE_EVENT, onClose);
    return () => window.removeEventListener(OPEN_CODE_EVENT, onClose);
  }, [cardId, onClose]);
  return (
    <Sheet open={Boolean(cardId && card)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent expandable className="max-w-3xl overflow-y-auto" aria-describedby="card-drawer-desc">
        {card && <DrawerBody runId={runId} cardId={card.id} spec={spec} />}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({ runId, cardId, spec }: { runId: string; cardId: string; spec?: SpecWire }) {
  const card = useRunStore((s) => s.cards[cardId]);
  const signals = useRunStore((s) => s.signals);
  const artifacts = useRunStore((s) => s.artifacts);
  const live = useRunStore((s) => s.live);
  const working = useRunStore((s) => {
    const c = s.cards[cardId];
    return Boolean(c && s.live && c.column === "in_progress" && s.agents[c.owner]?.state === "running");
  });
  const checklist = useMemo(() => (card ? acceptanceState(card, signals) : []), [card, signals]);
  const related = useMemo(() => artifacts.filter((a) => a.taskId === cardId), [artifacts, cardId]);
  if (!card) return null;
  const merges = mergeRanges(card);

  return (
    <>
      <SheetHeader>
        <SheetTitle>
          <span className="mr-2 font-mono text-sm text-muted-foreground">{card.id}</span>
          {card.title}
        </SheetTitle>
        <SheetDescription id="card-drawer-desc">
          Owned by {card.owner || "nobody"}. Columns move only when the scheduler says so.
        </SheetDescription>
        <div className="flex flex-wrap gap-1">
          <Badge variant="outline">{COLUMN_LABEL[card.column]}</Badge>
          {card.kind && <Badge variant="outline">{card.kind}</Badge>}
          {card.size && <Badge variant="secondary">{card.size}</Badge>}
          {card.round > 0 && <Badge variant="waiting">{`round ${card.round}`}</Badge>}
        </div>
      </SheetHeader>

      <WhyThisCard card={card} spec={spec} />

      <section className={SECTION} aria-label="Acceptance checklist">
        <h3 className={H}>Acceptance</h3>
        {checklist.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">No acceptance items on this card.</p>
        ) : (
          <ul className="m-0 grid list-none gap-1 p-0">
            {checklist.map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={item.met}
                  disabled
                  readOnly
                  aria-label={item.text}
                  className="mt-1"
                />
                <span>
                  {(() => {
                    const { check, rest } = splitCheckLine(item.text);
                    return (
                      <span className={cn(item.met && "text-muted-foreground")}>
                        {check && <span className="mr-1.5 rounded border border-primary/40 px-1 font-mono text-xs text-primary">{check}</span>}
                        {check ? rest : item.text}
                      </span>
                    );
                  })()}
                  {item.evidence && (
                    <span className="block text-xs text-muted-foreground">{item.evidence}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={SECTION} aria-label="Thread">
        <h3 className={H}>Thread</h3>
        {card.thread.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">Nothing on the thread yet.</p>
        ) : (
          <ol className="m-0 grid list-none gap-2 p-0">
            {card.thread.map((entry, i) => (
              <li
                key={i}
                data-kind={entry.kind}
                className={cn(
                  "rounded-md border border-border p-2 text-sm",
                  entry.kind === "human_comment" && "border-l-4 border-l-primary bg-accent",
                  entry.kind === "review_issue" && "border-l-4 border-l-status-failed",
                )}
              >
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <span aria-hidden>{THREAD_KIND_ICON[entry.kind] ?? "•"}</span>
                  <span className="font-medium">{THREAD_KIND_LABEL[entry.kind] ?? entry.kind}</span>
                  <span>{entry.author}</span>
                </div>
                {entry.kind === "handoff" ? (
                  <HandoffBody data={entry.data} />
                ) : (
                  <div className="whitespace-pre-wrap break-words">{entry.text}</div>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      {working && (
        <section className={SECTION} aria-label="Live changes">
          <h3 className={`${H} flex items-center justify-between`}>
            Live changes
            <button type="button" className="cursor-pointer bg-transparent p-0 text-xs font-normal text-primary hover:underline"
                    onClick={() => openInCode({ role: card.owner })}>
              Open in the Code tab
            </button>
          </h3>
          <div className="flex max-h-96 flex-col overflow-hidden rounded-md border border-border">
            <RoleChanges role={card.owner} />
          </div>
        </section>
      )}

      <section className={SECTION} aria-label="What the worker was told">
        <details>
          <summary className="cursor-pointer text-sm font-semibold">What the worker was told</summary>
          <div className="mt-2 flex max-h-[32rem] flex-col">
            <WhatItWasTold runId={runId} role={card.owner} card={card.id} live={live} />
          </div>
        </details>
      </section>

      <section className={SECTION} aria-label="Diffs">
        <h3 className={H}>Diffs</h3>
        {merges.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">No merges recorded for this card yet.</p>
        ) : (
          <ul className="m-0 grid list-none gap-2 p-0">
            {merges.map(([base, head]) => (
              <MergeDiff key={`${base}..${head}`} runId={runId} base={base} head={head} />
            ))}
          </ul>
        )}
      </section>

      <section className={SECTION} aria-label="Artifacts">
        <h3 className={H}>Artifacts</h3>
        {related.length === 0 ? (
          <p className="m-0 text-sm text-muted-foreground">No artifacts for this card.</p>
        ) : (
          <ul className="m-0 grid list-none gap-1 p-0 text-sm">
            {related.map((a) => {
              const href = a.url ?? a.servedPath;
              return (
                <li key={a.signalId} className="flex items-center gap-2">
                  <Badge variant="outline">{a.kind}</Badge>
                  {href ? (
                    <a href={href} target="_blank" rel="noreferrer" className="font-mono text-xs underline">
                      {a.path}
                    </a>
                  ) : (
                    <span className="font-mono text-xs">{a.path}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <CommentForm runId={runId} cardId={card.id} disabled={!live} />
    </>
  );
}

function MergeDiff({ runId, base, head }: { runId: string; base: string; head: string }) {
  const [state, setState] = useState<
    { kind: "idle" } | { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; diff: string }
  >({ kind: "idle" });

  async function load(): Promise<void> {
    setState({ kind: "loading" });
    try {
      // Exactly what this merge brought into main, however many commits it had.
      const res = await cardsApi.commitDiff(runId, head, base || undefined);
      setState({ kind: "ok", diff: res.diff });
    } catch (err) {
      setState({ kind: "error", message: err instanceof Error ? err.message : "Could not load the diff." });
    }
  }

  return (
    <li className="grid gap-1">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-mono text-xs">{`${base.slice(0, 8)}..${head.slice(0, 8)}`}</span>
        <Button variant="outline" size="sm" onClick={() => void load()} disabled={state.kind === "loading"}>
          {state.kind === "loading" ? "Loading..." : "View diff"}
        </Button>
      </div>
      {state.kind === "error" && (
        <p role="alert" className="m-0 text-xs text-destructive">{state.message}</p>
      )}
      {state.kind === "ok" && (
        <div className="max-h-96 overflow-auto rounded-md border border-border">
          <DiffText text={state.diff} />
        </div>
      )}
    </li>
  );
}

function CommentForm({ runId, cardId, disabled }: { runId: string; cardId: string; disabled: boolean }) {
  const comment = useCommentOnCard(runId);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent): Promise<void> {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    setError(null);
    try {
      await comment.mutateAsync({ taskId: cardId, text: body });
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not post the comment.");
    }
  }

  return (
    <form onSubmit={(e) => void submit(e)} className="mt-auto grid gap-2">
      <label className="grid gap-1 text-sm font-semibold">
        Comment
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="Your comment goes into the next brief for this card"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-normal text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </label>
      {error && (
        <p role="alert" className="m-0 text-sm text-destructive">{error}</p>
      )}
      <div className="flex justify-end">
        <Button type="submit" disabled={disabled || comment.isPending || !text.trim()}>
          {comment.isPending ? "Posting..." : "Post comment"}
        </Button>
      </div>
    </form>
  );
}
