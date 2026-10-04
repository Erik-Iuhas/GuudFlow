import { useEffect, useMemo, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { queryKeys, useRunCards } from "../api/queries";
import { createRunStore, type RunStore } from "../stores/runEvents";
import { useRunSocket, type RunSocketState } from "./useRunSocket";

const REFETCH_DEBOUNCE_MS = 300;

/**
 * The run view's live data in one call: a per-run store, its websocket, and the REST card
 * list (TanStack Query) seeded into the store and refetched when TASK_ASSIGNED announces a
 * new card.
 */
export function useRunLive(runId: string | undefined): { store: RunStore } & RunSocketState {
  const store = useMemo(() => createRunStore(runId ?? ""), [runId]);
  const qc = useQueryClient();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const socket = useRunSocket(runId, store, {
    onTaskAssigned: () => {
      if (!runId) return;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void qc.invalidateQueries({ queryKey: queryKeys.cards(runId) });
      }, REFETCH_DEBOUNCE_MS);
    },
  });

  const cards = useRunCards(runId);
  useEffect(() => {
    if (cards.data) store.getState().seedCards(cards.data);
  }, [cards.data, store]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return { store, ...socket };
}
