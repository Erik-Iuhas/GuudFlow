import { useCallback, useEffect, useRef, useState } from "react";
import { connectRunStream, type WsHandle } from "../api";
import type { RawSignal } from "../lib/runEvents.gen";
import type { RunStore } from "../stores/runEvents";

export type SocketStatus = "connecting" | "open" | "reconnecting" | "error";

export interface RunSocketState {
  status: SocketStatus;
  /** Set when the server answered with an error frame (e.g. run not found or finished). */
  error: string | null;
  /** Tear down and reconnect from scratch (also after the helper has given up retrying). */
  retry: () => void;
}

export interface RunSocketOptions {
  /** Called when a TASK_ASSIGNED signal arrives, so the caller can refetch the card list. */
  onTaskAssigned?: () => void;
}

/**
 * Connect `/ws/runs/{runId}` and feed the run-event store.
 *
 * URL and reconnect/backoff come from `connectRunStream` in api.ts (500 ms doubling to 5 s,
 * capped attempts), the same transport the current Run view uses. The server re-sends a full
 * snapshot after every reconnect; `ingest` dedupes by id, so replays are harmless.
 */
export function useRunSocket(
  runId: string | undefined,
  store: RunStore,
  options: RunSocketOptions = {},
): RunSocketState {
  const [status, setStatus] = useState<SocketStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const onTaskAssigned = useRef(options.onTaskAssigned);
  onTaskAssigned.current = options.onTaskAssigned;

  useEffect(() => {
    if (!runId) return;
    setStatus("connecting");
    setError(null);
    let handle: WsHandle | null = null;
    // A finished run: the server sends one snapshot (live: false) and closes normally.
    // That close is the end of the feed, not a drop, so do not reconnect.
    let finished = false;
    const { ingest, setLive } = store.getState();
    handle = connectRunStream(
      runId,
      (msg) => {
        if (msg.type === "snapshot" && Array.isArray(msg.signals)) {
          setLive(msg.live ?? true);
          finished = msg.live === false;
          ingest(msg.signals as unknown as RawSignal[]);
          setStatus("open");
        } else if (msg.type === "signal" && msg.signal) {
          const raw = msg.signal as unknown as RawSignal;
          ingest(raw);
          if (raw.type === "TASK_ASSIGNED") onTaskAssigned.current?.();
        } else if (msg.type === "error") {
          setError("run not found or finished");
          setStatus("error");
        }
      },
      undefined,
      (connected) => {
        if (!connected && finished) {
          handle?.close();
          return;
        }
        // A drop after "error" keeps the error; otherwise we are retrying.
        setStatus((prev) => (connected ? prev : prev === "error" ? prev : "reconnecting"));
      },
    );
    return () => {
      handle?.close();
    };
  }, [runId, store, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { status, error, retry };
}
