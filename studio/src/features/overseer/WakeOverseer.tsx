import { useState } from "react";
import { wakeOverseer } from "../../api";
import { Button } from "../../components/ui/button";

/** The Overseer tab's own wake button (worker context plan F4): an optional reason, then the
 * overseer wakes on its next check and its decision lands in this tab. */
export function WakeOverseer({ runId, present, active }: { runId: string; present: boolean; active: boolean }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
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
      await wakeOverseer(runId, reason.trim() || "Woken from the Overseer tab: check what is blocking the run.");
      setNote("Woken. Its decision appears below.");
      setReason("");
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
      <Button size="sm" disabled={busy} onClick={() => void wake()}>Wake overseer</Button>
      {note && <span className="text-xs text-muted-foreground" role="status">{note}</span>}
    </div>
  );
}
