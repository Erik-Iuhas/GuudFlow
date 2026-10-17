import { useEffect, useState } from "react";
import { useRunStore } from "../../stores/runStoreContext";
import { lastHumanAction } from "./receipts";

const KEEP_MS = 6000;

/** After an answer or a checkpoint decision: what was sent and that the run picked it up. The
 * settled line stays a few seconds after the run moved on, then goes. */
export function AnswerReceipt() {
  const signals = useRunStore((s) => s.signals);
  const pausedNow = useRunStore((s) => s.humanNeeded !== null);
  const live = useRunStore((s) => s.live);
  const action = lastHumanAction(signals);
  const [hiddenFor, setHiddenFor] = useState<string | null>(null);
  const key = action ? `${signals.length}:${action.text}` : null;
  useEffect(() => {
    if (!action?.settled || !key) return;
    const t = setTimeout(() => setHiddenFor(action.text), KEEP_MS);
    return () => clearTimeout(t);
  }, [action?.settled, action?.text, key]);
  if (!live || !action || pausedNow || hiddenFor === action.text) return null;
  return (
    <div role="status" data-testid="answer-receipt" data-settled={action.settled}
      className="rounded-md border border-border bg-muted/40 px-3 py-1.5 text-sm">
      {!action.settled && <span className="mr-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-status-running align-middle" aria-hidden />}
      {action.text}
    </div>
  );
}
