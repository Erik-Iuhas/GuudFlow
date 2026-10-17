import { useEffect, useState } from "react";
import type { TypedSignal } from "../../lib/runEvents.gen";

// What happened to something a person sent a worker (docs/RESPONSIVENESS_PLAN.md R1). Every
// stage comes from a real board signal after the one the send created; nothing is timed.

export type ReceiptStage =
  | "sent"        // the API took it; the board echo has not arrived yet
  | "queued"      // on the board; the worker is mid-turn and reads it in its next one
  | "waking"      // on the board, a wake was asked for, the worker has not started yet
  | "idle"        // on the board; the worker is not running and was not woken
  | "reading"     // the worker started a turn after it
  | "got"         // the worker's brief carried it (DELIVERED)
  | "answered";   // the worker finished the turn that carried it

export interface Receipt {
  stage: ReceiptStage;
  /** The worker's result summary once it answered. */
  reply?: string;
}

const ENDS = new Set(["AGENT_RESULT", "COMPLETE", "FAILED"]);

/** The stage of `ref` (the signal id the send returned) for `role`. */
export function receiptFor(signals: readonly TypedSignal[], ref: string, role: string,
  opts: { wake?: boolean; running?: boolean } = {}): Receipt {
  const at = signals.findIndex((s) => s.id === ref);
  if (at < 0) return { stage: "sent" };
  let started = false;
  let got = false;
  for (let i = at + 1; i < signals.length; i++) {
    const s = signals[i];
    if (s.type === "DELIVERED") {
      const d = s.data as { ref?: string; role?: string } | null;
      if (d?.ref === ref && d.role === role) got = true;
    } else if (s.from_agent === role) {
      if (s.type === "STARTED") started = true;
      else if (got && ENDS.has(s.type)) return { stage: "answered", reply: s.summary };
    }
  }
  if (got) return { stage: "got" };
  if (started) return { stage: "reading" };
  if (opts.running) return { stage: "queued" };
  return { stage: opts.wake ? "waking" : "idle" };
}

/** One line for a stage, about the worker `who`. */
export function receiptText(r: Receipt, who: string): string {
  switch (r.stage) {
    case "sent": return "Sent · reaching the board…";
    case "queued": return `On the board · ${who} is mid-turn and reads it as soon as this turn ends`;
    case "waking": return `On the board · waking ${who}…`;
    case "idle": return `On the board · ${who} reads it when it next starts`;
    case "reading": return `${who} started a turn · picking it up`;
    case "got": return `✓ ${who} has it and is working on it`;
    case "answered": return `✓ ${who} answered${r.reply ? `: ${r.reply}` : ""}`;
  }
}

/** Seconds since `since` (ms, client clock), ticking while `active`; for "how long so far". */
export function useElapsed(since: number | null, active = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null || !active) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [since, active]);
  return since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
}

/** Seconds a `pending` wait has lasted so far (0 when not pending): for long host-side calls. */
export function usePendingClock(pending: boolean): number {
  const [since, setSince] = useState<number | null>(null);
  useEffect(() => setSince(pending ? Date.now() : null), [pending]);
  return useElapsed(since, pending);
}

/** "0:42", "3:05". */
export function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export type WakeStage = "sent" | "waiting" | "woken" | "looking" | "decided" | "failed";

/** What the overseer did after the owner's wake (`ref`: the ESCALATION the wake posted). */
export function overseerWakeFor(signals: readonly TypedSignal[], ref: string): { stage: WakeStage; detail?: string } {
  const at = signals.findIndex((s) => s.id === ref);
  if (at < 0) return { stage: "sent" };
  let stage: WakeStage = "waiting";
  for (let i = at + 1; i < signals.length; i++) {
    const s = signals[i];
    const overseer = (s.from_agent ?? "").includes("overseer");
    if (!overseer) continue;
    if (s.type === "OVERSEER_INTERVENTION" || s.type === "AGENT_RESULT") return { stage: "decided", detail: s.summary };
    if (s.type === "FAILED" || s.type === "HUMAN_NEEDED") return { stage: "failed", detail: s.summary };
    if (s.type === "STARTED") stage = "looking";
    else if (s.type === "OVERSEER_WAKE" && stage === "waiting") stage = "woken";
  }
  return { stage };
}

export const WAKE_TEXT: Record<WakeStage, string> = {
  sent: "Sent · reaching the board…",
  waiting: "On the board · the overseer picks it up at its next check (a few seconds)",
  woken: "The overseer is waking…",
  looking: "The overseer is looking at the run",
  decided: "✓ The overseer decided",
  failed: "The overseer could not decide; the run needs you",
};

/** The person's last answer or checkpoint decision and whether the run has moved on from it
 * (docs/RESPONSIVENESS_PLAN.md R3). Null when there is nothing to confirm. */
export function lastHumanAction(signals: readonly TypedSignal[]): { text: string; settled: boolean } | null {
  let at = -1;
  for (let i = signals.length - 1; i >= 0; i--) {
    if (signals[i].type === "HUMAN_RESPONDED") { at = i; break; }
  }
  if (at < 0) return null;
  const sig = signals[at];
  let detail: { answer?: string; plan_review?: string } = {};
  try {
    detail = sig.detail ? JSON.parse(sig.detail) : {};
  } catch {
    detail = {};
  }
  let asker: string | null = null;
  for (let i = at - 1; i >= 0; i--) {
    if (signals[i].type === "HUMAN_NEEDED") { asker = signals[i].from_agent; break; }
  }
  const later = signals.slice(at + 1);
  if (detail.plan_review === "sent_back") {
    const replanned = later.some((s) => s.type === "HUMAN_NEEDED" || (s.type === "COMPLETE" && s.from_agent === asker));
    return { text: replanned ? `✓ ${asker ?? "The planner"} planned again`
      : `Plan sent back: “${detail.answer ?? ""}” · ${asker ?? "the planner"} is planning again with your note`, settled: replanned };
  }
  if (detail.plan_review === "approved") {
    const started = later.some((s) => s.type === "TASK_MOVED" && (s.data as { to?: string } | null)?.to === "in_progress");
    return { text: started ? "✓ Plan approved · the first cards started" : "Plan approved · the first cards are starting", settled: started };
  }
  const who = asker ?? "the team";
  const resumed = later.some((s) => s.from_agent === asker && s.type !== "DELIVERED");
  return { text: resumed ? `✓ ${who} carried on after your answer`
    : `You answered: “${detail.answer ?? sig.summary}” · ${who} is resuming`, settled: resumed };
}
