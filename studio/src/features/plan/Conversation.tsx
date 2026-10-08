import { useEffect, useRef, useState } from "react";
import { Alert, Label, Textarea } from "../../components/ui/form-controls";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/utils";
import type { PlanMessageWire, PlanMode } from "./api";
import { usePmName } from "../pm/api";
import { Markdown } from "../../lib/markdown";
import { foldThread, timeLabel } from "./model";

export interface ConversationProps {
  messages: readonly PlanMessageWire[];
  /** True while the project manager is working on a turn. */
  pending: boolean;
  /** Sends the composer text; rejecting keeps the text in the composer. */
  onSend: (text: string, mode: PlanMode) => Promise<unknown>;
  /** Asks for the first draft (the empty state). */
  onStart: () => Promise<unknown>;
  /** Pending-state text for the draft button, e.g. an error from the last start. */
  error?: string | null;
  /** The open draft's id; its thread stays open and earlier drafts' threads fold. */
  currentDraftId?: string | null;
}

/** "draft 3" from a draft id such as "draft-3" or "d3". */
function draftNumber(id: string | null): string | null {
  const m = id?.match(/(\d+)$/);
  return m ? m[1] : null;
}

/** The message that asks the project manager to repair what it left out of the draft. */
export function fixRequest(problems: string[]): string {
  return ("Please fix the changes you left out of the draft last turn. Send only those, " +
    "corrected:\n" + problems.map((p) => `- ${p}`).join("\n"));
}

const STATUS_LABEL: Record<string, string> = {
  partial: "some changes left out",
  failed: "turn failed, nothing added",
};

function Message({ m, pmName, onFix }: { m: PlanMessageWire; pmName: string; onFix?: () => void }) {
  const lead = m.author === "tech_lead";
  const pm = m.author === "pm" || lead;
  const draft = draftNumber(m.draft_id);
  const who = lead ? "Tech lead" : pmName;
  return (
    <li className="flex gap-3" data-testid={`msg-${m.id}`}>
      <span
        aria-hidden="true"
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
          lead ? "bg-status-running/20 text-foreground" : pm ? "bg-primary text-primary-foreground"
            : "bg-secondary text-secondary-foreground",
        )}
      >
        {pm ? who.charAt(0).toUpperCase() : "You"}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">{pm ? who : "You"}</span>
          {pm && <span>{lead ? "feasibility note" : "project manager"}</span>}
          <time>{timeLabel(m.at)}</time>
          {draft && <span>{pm ? `proposed changes in draft ${draft}` : `draft ${draft}`}</span>}
          {m.mode === "discuss" && !pm && <span>just discussing</span>}
          {pm && typeof m.cost_usd === "number" && <span>${m.cost_usd.toFixed(2)}</span>}
          {m.status && STATUS_LABEL[m.status] && (
            <span className={m.status === "failed" ? "text-status-failed" : "text-status-blocked"}
              data-testid={`turn-status-${m.id}`}>
              {STATUS_LABEL[m.status]}
            </span>
          )}
        </div>
        {pm ? (
          <Markdown text={m.text} className="mt-1 break-words text-sm" />
        ) : (
          <p className="mt-1 whitespace-pre-wrap break-words text-sm">{m.text}</p>
        )}
        {onFix && (
          <Button size="sm" variant="outline" className="mt-2" onClick={onFix}>
            Ask {pmName} to fix these
          </Button>
        )}
      </div>
    </li>
  );
}

/** The plan conversation: the thread, an empty state and the composer. */
export function Conversation({ messages, pending, onSend, onStart, error, currentDraftId }: ConversationProps) {
  const pmName = usePmName();
  const [text, setText] = useState("");
  const [mode, setMode] = useState<PlanMode>("update");
  const [failure, setFailure] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, pending]);

  // Auto-grow with the content; the user can still drag the corner.
  useEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(el.scrollHeight, 0)}px`;
  }, [text]);

  const canSend = text.trim().length > 0 && !pending;

  async function send(): Promise<void> {
    if (!canSend) return;
    setFailure(null);
    try {
      await onSend(text.trim(), mode);
      setText("");
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    }
  }

  async function start(): Promise<void> {
    setFailure(null);
    try {
      await onStart();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    }
  }

  // Only the latest PM turn can be repaired, and only while it is still the latest message.
  const last = messages[messages.length - 1];
  const fixableId = last && last.author === "pm" && (last.problems?.length ?? 0) > 0
    && (last.status === "partial" || last.status === "failed") ? last.id : null;

  async function fix(problems: string[]): Promise<void> {
    setFailure(null);
    try {
      await onSend(fixRequest(problems), "update");
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e));
    }
  }

  const empty = messages.length === 0 && !pending;
  const thread = foldThread(messages, currentDraftId ?? null);
  const shownError = failure ?? error ?? null;

  return (
    <section aria-label="Conversation" className="flex min-w-0 flex-1 flex-col gap-4">
      {empty ? (
        <div className="rounded-lg border border-dashed border-border p-6 text-sm">
          <p className="font-medium">No plan yet</p>
          <p className="mt-1 text-muted-foreground">
            {pmName} reads your project goal and files, then proposes the first draft of the spec:
            requirements with checks, the decisions behind them, and the first work items. You approve what you agree
            with.
          </p>
          <Button className="mt-4" onClick={() => void start()}>
            Draft the first plan
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {thread.folded.map((g) => (
            <details key={g.key} className="rounded-md border border-border px-3 py-2 text-sm" data-testid="folded-thread">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                {g.label} · {g.messages.length} message{g.messages.length === 1 ? "" : "s"}
              </summary>
              <ol className="mt-3 flex flex-col gap-4">
                {g.messages.map((m) => (
                  <Message key={m.id} m={m} pmName={pmName} />
                ))}
              </ol>
            </details>
          ))}
          <ol className="flex flex-col gap-4" aria-live="polite">
            {thread.open.map((m) => (
              <Message key={m.id} m={m} pmName={pmName}
                onFix={m.id === fixableId && !pending ? () => void fix(m.problems ?? []) : undefined} />
            ))}
          </ol>
        </div>
      )}
      {pending && (
        <p role="status" className="text-sm text-muted-foreground">
          {pmName} is drafting…
        </p>
      )}
      <div ref={endRef} />
      {shownError && <Alert variant="error">{shownError}</Alert>}
      {!empty && (
        <div className="flex flex-col gap-2">
          <Label htmlFor="plan-composer">Answer, push back, or think out loud. It grows as you write.</Label>
          <Textarea
            id="plan-composer"
            ref={areaRef}
            rows={6}
            className="min-h-[9rem] resize-y"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="flex flex-wrap items-center gap-3">
            <div role="group" aria-label="What Send does" className="inline-flex gap-1">
              <Button
                type="button"
                size="sm"
                variant={mode === "update" ? "default" : "outline"}
                aria-pressed={mode === "update"}
                onClick={() => setMode("update")}
              >
                Update the draft
              </Button>
              <Button
                type="button"
                size="sm"
                variant={mode === "discuss" ? "default" : "outline"}
                aria-pressed={mode === "discuss"}
                onClick={() => setMode("discuss")}
              >
                Just discuss
              </Button>
            </div>
            <span className="text-xs text-muted-foreground">Just discuss: the plan isn't touched</span>
            <Button className="ml-auto" disabled={!canSend} onClick={() => void send()}>
              Send
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
