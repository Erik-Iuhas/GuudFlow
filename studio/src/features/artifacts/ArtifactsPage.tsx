import { useEffect, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { getCards } from "../../api";
import { useRunSocket } from "../../hooks/useRunSocket";
import { createRunStore } from "../../stores/runEvents";
import { RunStoreProvider } from "../../stores/runStoreContext";
import { ArtifactGallery } from "./ArtifactGallery";

/** Route `/runs/:id/artifacts`: the gallery with its own run store and socket. */
export function ArtifactsPage() {
  const { id = "" } = useParams<{ id: string }>();
  const store = useMemo(() => createRunStore(id), [id]);
  const socket = useRunSocket(id, store);

  useEffect(() => {
    // Card titles label the task filter; the gallery works without them.
    getCards(id)
      .then((cards) => store.getState().seedCards(cards))
      .catch(() => undefined);
  }, [id, store]);

  return (
    <div className="page flex flex-col gap-3">
      <div className="flex items-baseline gap-3">
        <h1 className="m-0">Artifacts</h1>
        <Link to={`/runs/${encodeURIComponent(id)}`} className="text-sm">
          Back to run {id}
        </Link>
        {socket.status !== "open" && (
          <span className="text-xs text-muted-foreground" role="status">
            {socket.error ?? (socket.status === "connecting" ? "Connecting…" : "Reconnecting…")}
          </span>
        )}
      </div>
      <RunStoreProvider store={store}>
        <ArtifactGallery runId={id} />
      </RunStoreProvider>
    </div>
  );
}
