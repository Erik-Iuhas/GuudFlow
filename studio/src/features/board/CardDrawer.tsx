import { useMemo, useState, type FormEvent } from "react";
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
} from "./boardModel";
import { useCommentOnCard } from "./useCardMutations";

interface Props {
  runId: string;
  cardId: string | null;
  onClose: () => void;
}

const SECTION = "grid gap-2";
const H = "m-0 text-sm font-semibold";

/** Side drawer for one card: acceptance checklist, thread, diffs, artifacts and a comment box. */
export function CardDrawer({ runId, cardId, onClose }: Props) {
  const card = useRunStore((s) => (cardId ? s.cards[cardId] : undefined));
  return (
    <Sheet open={Boolean(cardId && card)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="max-w-xl overflow-y-auto" aria-describedby="card-drawer-desc">
        {card && <DrawerBody runId={runId} cardId={card.id} />}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({ runId, cardId }: { runId: string; cardId: string }) {
  const card = useRunStore((s) => s.cards[cardId]);
  const signals = useRunStore((s) => s.signals);
  const artifacts = useRunStore((s) => s.artifacts);
  const live = useRunStore((s) => s.live);
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
                  <span className={cn(item.met && "text-muted-foreground")}>{item.text}</span>
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
                <div className="whitespace-pre-wrap break-words">{entry.text}</div>
              </li>
            ))}
          </ol>
        )}
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
      // TODO: the backend only exposes a single-commit diff (`GET /git/diff?ref=`). Show the
      // head commit of the merged range until a range diff (base..head) endpoint exists.
      const res = await cardsApi.commitDiff(runId, head);
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
        <pre className="m-0 max-h-72 overflow-auto rounded-md border border-border bg-muted p-2 font-mono text-xs">
          {state.diff.split("\n").map((line, i) => (
            <div
              key={i}
              className={cn(
                line.startsWith("+") && !line.startsWith("+++") && "text-status-complete",
                line.startsWith("-") && !line.startsWith("---") && "text-status-failed",
              )}
            >
              {line || " "}
            </div>
          ))}
        </pre>
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
    <form onSubmit={(e) => void submit(e)} className="mt-auto grid gap-2" aria-label="Comment on this card">
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
