import { Alert } from "../../components/ui/form-controls";
import { Button } from "../../components/ui/button";
import { useInterrupted, useSalvage } from "./api";

/** Sessions cut off by a restart: send what they merged to review (FIELD_NOTES F3). */
export function Interrupted({ projectId }: { projectId: string }) {
  const runs = useInterrupted(projectId);
  const salvage = useSalvage(projectId);
  if (!runs.data?.length) return null;
  return (
    <section aria-label="Interrupted sessions" className="flex flex-col gap-2" data-testid="interrupted">
      {runs.data.map((r) => (
        <Alert key={r.run_id} variant="error" className="flex flex-wrap items-center gap-3">
          <span className="min-w-0 flex-1">
            Session <span className="font-mono">{r.run_id}</span> stopped without finishing
            {r.last_activity ? ` (last activity ${new Date(r.last_activity).toLocaleString()})` : ""}: {r.cards_done} of{" "}
            {r.cards_total} cards done, holding {r.items.join(", ")}. Send what it merged to review; unfinished cards go
            back to the backlog.
          </span>
          <Button size="sm" disabled={salvage.isPending} onClick={() => salvage.mutate(r.run_id)}>
            Send to review
          </Button>
        </Alert>
      ))}
      {salvage.error && <Alert variant="error">{salvage.error.message}</Alert>}
    </section>
  );
}
