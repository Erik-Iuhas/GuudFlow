import { useState } from "react";
import { Button } from "../../components/ui/button";
import { WakeOverseer } from "../overseer/WakeOverseer";

/**
 * Whether the run has an overseer, and a way to wake it by hand. A run whose team has no
 * planner uses plain roles and starts no overseer; saying so beats an absent node. Waking opens
 * the same in-app form as the Overseer tab (a reason box and the wake's live state), never a
 * browser dialog.
 */
export function OverseerBar({ runId, present, active }: { runId: string; present: boolean; active: boolean }) {
  const [open, setOpen] = useState(false);
  if (!present) {
    return (
      <p className="m-0 rounded-md border border-dashed border-border px-3 py-1.5 text-xs text-muted-foreground" data-testid="overseer-bar">
        No overseer on this run: it only runs when the team has a planner (task cards). Ask Jack to
        add a planner in the Plan tab.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-1 text-xs text-muted-foreground" data-testid="overseer-bar">
      <div className="flex flex-wrap items-center gap-2">
        <span>Overseer: watching; it wakes on stalls.</span>
        {active && (
          <Button size="sm" variant="outline" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? "Close" : "Wake overseer…"}
          </Button>
        )}
      </div>
      {active && open && (
        <div className="rounded-md border border-border bg-card">
          <WakeOverseer runId={runId} present active />
        </div>
      )}
    </div>
  );
}
