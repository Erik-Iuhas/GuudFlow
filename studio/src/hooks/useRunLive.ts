import { useEffect, useMemo, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useStore } from "zustand";
import { queryKeys, useRunCards } from "../api/queries";
import { useRunCosts } from "../features/report/api";
import { createRunStore, type RunStore } from "../stores/runEvents";
import { useRunSocket, type RunSocketState } from "./useRunSocket";

const REFETCH_DEBOUNCE_MS = 300;
/** How often a live run's gateway spend is re-read (it moves during a turn, signals do not). */
export const COSTS_POLL_MS = 5_000;

/**
 * The run view's live data in one call: a per-run store, its websocket, and the REST card
 * list (TanStack Query) seeded into the store and refetched when TASK_ASSIGNED announces a
 * new card. While the run is live, gateway spend is polled into the store too.
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

  const live = useStore(store, (s) => s.live);
  const costs = useRunCosts(runId, live ? COSTS_POLL_MS : false);
  useEffect(() => {
    if (costs.data) store.getState().seedCosts(costs.data.rows);
  }, [costs.data, store]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return { store, ...socket };
}
