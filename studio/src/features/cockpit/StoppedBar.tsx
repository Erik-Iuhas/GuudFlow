import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, invokeApi } from "../../api";
import { Button } from "../../components/ui/button";
import { useRunStore } from "../../stores/runStoreContext";

/**
 * Shown instead of the comment box once a run has stopped or finished (worker context plan
 * I2): nothing can read a message any more, so the bar says what happened and offers what
 * can: continue with the open cards (with a note every worker reads first) or review it.
 */
export function StoppedBar({ runId, projectId, finished }: {
  runId: string;
  projectId?: string | null;
  finished: boolean;
}) {
  const stoppedAt = useRunStore((s) => s.stoppedAt);
  const open = useRunStore((s) => Object.values(s.cards).filter((c) => c.column !== "done").length);
  // Stop lets running turns finish (DESIGN rule 7): say who is still finishing (R4).
  // A joined string keeps the selector's result stable between renders.
  const finishingText = useRunStore((s) => (s.stoppedAt && s.live
    ? Object.values(s.agents).filter((a) => a.state === "running").map((a) => s.names[a.role] ?? a.role).join("\u0000")
    : ""));
  const finishing = finishingText ? finishingText.split("\u0000") : [];
  const navigate = useNavigate();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [gate, setGate] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const when = stoppedAt ? new Date(stoppedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  const headline = stoppedAt
    ? `Stopped by you${when ? ` at ${when}` : ""}${open ? ` with ${open} card${open === 1 ? "" : "s"} open` : ""}.`
    : finished ? "This session has finished." : "This session is no longer running.";

  async function continueRun(confirm: boolean) {
    if (!projectId) return;
    setBusy(true);
    setError(null);
    try {
      const res = await invokeApi.invoke(projectId, {
        continue_run: runId, ...(note.trim() ? { continue_note: note.trim() } : {}),
        ...(confirm ? { confirm: true } : {}),
      });
      if (res.accepted) {
        navigate(`/runs/${res.run_id}`);
        return;
      }
      setGate(res.message ?? "Check the cost before starting.");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-2 rounded-md border border-border bg-muted/40 p-3" data-testid="stopped-bar" role="status">
      {finishing.length > 0 ? (
        <p className="m-0 text-sm font-medium" data-testid="stopping">
          <span className="mr-1.5 inline-block h-2 w-2 animate-pulse rounded-full bg-status-waiting align-middle" aria-hidden />
          Stopping: {finishing.join(", ")} {finishing.length === 1 ? "is" : "are"} finishing the current turn. Nothing new starts.
        </p>
      ) : (
        <p className="m-0 text-sm font-medium">{headline} Nothing is listening here any more.</p>
      )}
      {projectId ? (
        <>
          <label className="grid gap-1 text-xs text-muted-foreground">
            A note for the team (every worker reads it first when you continue)
            <textarea
              className="min-h-[3rem] rounded-md border border-input bg-background p-2 text-sm text-foreground"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. Use Python and pygame only; T4 is done by hand."
              aria-label="Note for the continuation"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={busy} onClick={() => void continueRun(gate !== null)}>
              {gate ? "Start anyway" : "Continue with the open cards"}
            </Button>
            <Button size="sm" variant="outline" asChild>
              <Link to={`/projects/${encodeURIComponent(projectId)}?tab=work`}>Send to review</Link>
            </Button>
          </div>
          {gate && <p className="m-0 text-xs text-muted-foreground">{gate}</p>}
          {error && <p className="m-0 text-xs text-destructive" role="alert">{error}</p>}
        </>
      ) : (
        <p className="m-0 text-xs text-muted-foreground">Start a new run from the project to continue.</p>
      )}
    </div>
  );
}
