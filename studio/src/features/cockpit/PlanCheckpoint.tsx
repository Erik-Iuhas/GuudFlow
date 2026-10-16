import { useState } from "react";
import { approvePlan, sendPlanBack } from "../../api";
import { Button } from "../../components/ui/button";

/**
 * The plan checkpoint (worker context plan H5): the planner's cards are on the board and
 * nothing starts until a person approves, edits them, or sends the plan back with a note.
 */
export function PlanCheckpoint({ runId, role, cards, onEdit }: {
  runId: string;
  role: string | null;
  cards: string[];
  onEdit: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setNote("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="region" aria-label="Review the plan" data-testid="plan-checkpoint"
      className="flex flex-col gap-2 rounded-md border border-status-waiting/40 bg-status-waiting/10 px-3 py-2 text-sm">
      <p className="m-0">
        <strong>{role ?? "The planner"} planned {cards.length} card(s).</strong> Nothing starts until you
        approve. Edit cards on the board, or send the plan back with what to change.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => void act(() => approvePlan(runId))}>Approve the plan</Button>
        <Button size="sm" variant="outline" onClick={onEdit}>Edit the cards</Button>
        <input className="h-8 min-w-[16rem] flex-1 rounded-md border border-input bg-background px-2 text-sm"
          aria-label="What to change" placeholder="What should change? e.g. one module per level"
          value={note} onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" variant="outline" disabled={busy || note.trim().length < 3}
          onClick={() => void act(() => sendPlanBack(runId, note.trim()))}>
          Send back
        </Button>
      </div>
      {error && <p role="alert" className="m-0 text-xs text-destructive">{error}</p>}
    </div>
  );
}
