import { Link } from "react-router-dom";
import { Alert } from "../../components/ui/form-controls";
import { Button } from "../../components/ui/button";
import { preflightApi, usePreflight, usePreflightAction, type PreflightWire } from "./api";

/**
 * The Work tab's way into a prepared session (worker context plan H1): prepare the chosen
 * items with the tech lead, then start the approved plan.
 */
export function PrepareSession({ projectId, chosen, starting, onStart }: {
  projectId: string;
  chosen: string[];
  starting?: boolean;
  onStart: (plan: PreflightWire) => void;
}) {
  const plan = usePreflight(projectId).data;
  const prepare = usePreflightAction(projectId, (items: string[]) => preflightApi.prepare(projectId, items));
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-border p-2 text-sm" data-testid="prepare-session">
      {plan ? (
        <>
          <span className="mr-auto">
            {plan.status === "approved"
              ? `Prepared session ${plan.id} is approved (${plan.plan?.tasks.length ?? 0} cards).`
              : `Prepared session ${plan.id} is a draft${plan.open_questions.length ? ` with ${plan.open_questions.length} question(s) for you` : ""}.`}
          </span>
          <Link className="text-xs underline" to="?tab=design">Open in Design</Link>
          {plan.status === "approved" && (
            <Button size="sm" disabled={starting} onClick={() => onStart(plan)}>Start the prepared session</Button>
          )}
        </>
      ) : (
        <>
          <span className="mr-auto text-muted-foreground">
            Settle the cards and the design with the tech lead before the session starts.
          </span>
          <Button size="sm" variant="outline" disabled={!chosen.length || prepare.isPending}
            onClick={() => prepare.mutate(chosen)}>
            {prepare.isPending ? "The tech lead is planning…" : "Prepare with the tech lead"}
          </Button>
        </>
      )}
      {prepare.error && <Alert variant="error" className="w-full">{prepare.error.message}</Alert>}
    </div>
  );
}
