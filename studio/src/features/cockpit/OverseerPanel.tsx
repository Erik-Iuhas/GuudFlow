import { Badge } from "../../components/ui/badge";
import { useRunStore } from "../../stores/runStoreContext";
import { interventionTarget } from "./model";

/** The overseer's interventions: what it did, to what, and why. Newest first. */
export function OverseerPanel() {
  const interventions = useRunStore((s) => s.interventions);
  if (interventions.length === 0) {
    return (
      <p className="m-0 p-3 text-sm text-muted-foreground">
        No interventions. The overseer has not stepped in on this run.
      </p>
    );
  }
  return (
    <ol aria-label="Overseer interventions" className="m-0 flex list-none flex-col gap-2 p-2">
      {[...interventions].reverse().map((iv) => (
        <li
          key={iv.signalId}
          data-testid="intervention"
          className="rounded-md border border-border bg-card p-3 text-sm"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="waiting">{iv.data.type}</Badge>
            <span className="text-xs text-muted-foreground">{interventionTarget(iv.data)}</span>
            {iv.data.needs_human && <Badge variant="blocked">needs human</Badge>}
          </div>
          <p className="m-0 mt-1">{iv.data.rationale}</p>
          {iv.data.addendum && <p className="m-0 mt-1 text-xs text-muted-foreground">Brief: {iv.data.addendum}</p>}
          {iv.data.question && <p className="m-0 mt-1 text-xs">Question: {iv.data.question}</p>}
          {iv.result && <p className="m-0 mt-1 text-xs text-muted-foreground">Result: {iv.result}</p>}
        </li>
      ))}
    </ol>
  );
}
