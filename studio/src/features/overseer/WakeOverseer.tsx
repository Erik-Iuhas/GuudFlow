import { useState } from "react";
import { wakeOverseer } from "../../api";
import { Button } from "../../components/ui/button";
import { useRunStore } from "../../stores/runStoreContext";
import { WAKE_TEXT, clock, overseerWakeFor, useElapsed } from "../cockpit/receipts";

/** The Overseer tab's own wake button (worker context plan F4): an optional reason, then the
 * overseer wakes on its next check and its decision lands in this tab. */
export function WakeOverseer({ runId, present, active }: { runId: string; present: boolean; active: boolean }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [sent, setSent] = useState<{ ref: string; at: number } | null>(null);
  const signals = useRunStore((s) => s.signals);
  const state = sent ? overseerWakeFor(signals, sent.ref) : null;
  const settled = state?.stage === "decided" || state?.stage === "failed";
  const seconds = useElapsed(sent?.at ?? null, !settled);
  if (!present) {
    return (
      <p className="m-0 px-3 py-2 text-xs text-muted-foreground" data-testid="wake-overseer">
        No overseer on this run: it only runs when the team has a planner (task cards).
      </p>
    );
  }
  if (!active) return null;
  async function wake() {
    setBusy(true);
    try {
      const at = Date.now();
      setNote(null);
      const res = await wakeOverseer(runId, reason.trim() || "Woken from the Overseer tab: check what is blocking the run.");
      setReason("");
      if (res.signal_id) setSent({ ref: res.signal_id, at });
      else setNote("Woken. Its decision appears below.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2" data-testid="wake-overseer">
      <input
        className="h-8 min-w-[12rem] flex-1 rounded-md border border-input bg-background px-2 text-sm"
        aria-label="Why wake the overseer"
        placeholder="What should it look at? (optional)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") void wake(); }}
      />
      <Button size="sm" disabled={busy || (!!state && !settled)} onClick={() => void wake()}>
        {busy ? "Waking…" : "Wake overseer"}
      </Button>
      {note && <span className="text-xs text-muted-foreground" role="status">{note}</span>}
      {state && (
        <span className={`text-xs ${settled ? "" : "text-muted-foreground"}`} role="status" data-testid="wake-state" data-stage={state.stage}>
          {!settled && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current align-middle" aria-hidden />}
          {WAKE_TEXT[state.stage]}{state.detail ? `: ${state.detail}` : ""}
          {!settled && <span className="ml-1 tabular-nums">{clock(seconds)}</span>}
        </span>
      )}
    </div>
  );
}
