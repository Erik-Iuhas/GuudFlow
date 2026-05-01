import { useCallback, useEffect, useRef, useState } from "react";
import { runsApi } from "../api";
import type { DesksData, Task } from "../types";
import { useRunWs } from "../contexts/RunWsContext";

const POLL_MS = 5000;
const DEBOUNCE_MS = 500;

function normalise(raw: { desks: Record<string, unknown[]>; tasks_by_id: Record<string, unknown> }): DesksData {
  return {
    desks: raw.desks as Record<string, Task[]>,
    tasksById: raw.tasks_by_id as Record<string, Task>,
  };
}

export function useDesks(runId: string | null): {
  data: DesksData | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
} {
  const [data, setData] = useState<DesksData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ws = useRunWs();

  const fetch = useCallback(async () => {
    if (!runId) return;
    try {
      const raw = await runsApi.desks(runId);
      if (typeof window !== "undefined" && (window as unknown as { DEBUG_DESKS?: boolean }).DEBUG_DESKS) {
        console.debug("[useDesks] raw API response:", raw);
      }
      setData(normalise(raw));
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [runId]);

  const scheduleDebounced = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => { void fetch(); }, DEBOUNCE_MS);
  }, [fetch]);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    void fetch();

    if (ws) {
      // Subscribe to WS signals; refetch on TASK_* or COMMIT
      const unsub = ws.subscribe((signal) => {
        if (
          signal.type.startsWith("TASK_") ||
          signal.type === "COMMIT" ||
          signal.type === "STARTED" ||
          signal.type === "COMPLETE"
        ) {
          scheduleDebounced();
        }
      });
      return () => {
        unsub();
        if (debounceRef.current) clearTimeout(debounceRef.current);
      };
    } else {
      // Fallback: poll every 5s when outside RunWsProvider
      function schedulePoll() {
        pollRef.current = setTimeout(() => {
          void fetch();
          schedulePoll();
        }, POLL_MS);
      }
      schedulePoll();
      return () => {
        if (pollRef.current) clearTimeout(pollRef.current);
      };
    }
  }, [runId, ws, fetch, scheduleDebounced]);

  return { data, loading, error, refetch: fetch };
}
