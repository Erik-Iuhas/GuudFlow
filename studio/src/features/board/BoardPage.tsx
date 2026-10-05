import { useEffect, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useInvalidateCards } from "../../api/queries";
import { Button } from "../../components/ui/button";
import { useRunSocket } from "../../hooks/useRunSocket";
import { createRunStore } from "../../stores/runEvents";
import { RunStoreProvider } from "../../stores/runStoreContext";
import { TaskBoard } from "./TaskBoard";

/**
 * Standalone board route (`/runs/:runId/board`): owns a run store fed by the run websocket and
 * mounts `<TaskBoard>`. The Run cockpit mounts `<TaskBoard runId>` itself, under its own store.
 */
export function BoardPage() {
  const { runId } = useParams<{ runId: string }>();
  if (!runId) return <div className="page"><p>Missing run id.</p></div>;
  return <BoardPageInner runId={runId} />;
}

function BoardPageInner({ runId }: { runId: string }) {
  const store = useMemo(() => createRunStore(runId), [runId]);
  const invalidateCards = useInvalidateCards(runId);
  const socket = useRunSocket(runId, store, { onTaskAssigned: invalidateCards });

  // The socket answers an error frame for a finished run: the cards still load over REST,
  // but the board is read-only.
  useEffect(() => {
    if (socket.status === "error") store.getState().setLive(false);
  }, [socket.status, store]);

  return (
    <div className="page grid gap-3">
      <div className="flex items-center gap-3">
        <Link to={`/runs/${encodeURIComponent(runId)}`}>Back to run</Link>
        <span className="font-mono text-xs text-muted-foreground">{runId}</span>
        {socket.status === "reconnecting" && (
          <span className="text-xs text-muted-foreground">Reconnecting...</span>
        )}
        {socket.status === "error" && (
          <>
            <span className="text-xs text-muted-foreground">
              {socket.error ?? "Live updates unavailable"}
            </span>
            <Button size="sm" variant="outline" onClick={socket.retry}>Retry</Button>
          </>
        )}
      </div>
      <RunStoreProvider store={store}>
        <TaskBoard runId={runId} />
      </RunStoreProvider>
    </div>
  );
}
