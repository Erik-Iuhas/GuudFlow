import { useState } from "react";
import { wakeOverseer } from "../../api";
import { Button } from "../../components/ui/button";

/**
 * Whether the run has an overseer, and a way to wake it by hand. A run whose team has no
 * planner uses plain roles and starts no overseer; saying so beats an absent node.
 */
export function OverseerBar({ runId, present, active }: { runId: string; present: boolean; active: boolean }) {
  const [note, setNote] = useState<string | null>(null);
  if (!present) {
    return (
      <p className="m-0 rounded-md border border-dashed border-border px-3 py-1.5 text-xs text-muted-foreground" data-testid="overseer-bar">
        No overseer on this run: it only runs when the team has a planner (task cards). Ask Jack to
        add a planner in the Plan tab.
      </p>
    );
  }
  async function wake() {
    const text = window.prompt("What should the overseer look at?", "The run looks stuck. Check what is blocking it.");
    if (text === null) return;
    try {
      await wakeOverseer(runId, text);
      setNote("Overseer woken; its decision appears on the Overseer tab.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground" data-testid="overseer-bar">
      <span>Overseer: watching; it wakes on stalls.</span>
      {active && <Button size="sm" variant="outline" onClick={() => void wake()}>Wake overseer</Button>}
      {note && <span>{note}</span>}
    </div>
  );
}
