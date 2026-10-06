import { createContext, useContext, type ReactNode } from "react";
import { useStore } from "zustand";
import type { RunState, RunStore } from "./runEvents";

const RunStoreContext = createContext<RunStore | null>(null);

/** Provides one run's store to the components below it. */
export function RunStoreProvider({ store, children }: { store: RunStore; children?: ReactNode }) {
  return <RunStoreContext.Provider value={store}>{children}</RunStoreContext.Provider>;
}

/** Subscribe to a slice of the current run store: `useRunStore((s) => s.cards[id])`. */
export function useRunStore<T>(selector: (state: RunState) => T): T {
  const store = useContext(RunStoreContext);
  if (store === null) throw new Error("useRunStore must be used within RunStoreProvider");
  return useStore(store, selector);
}

/** The store itself, for imperative access (e.g. `store.getState().ingest`). */
export function useRunStoreApi(): RunStore {
  const store = useContext(RunStoreContext);
  if (store === null) throw new Error("useRunStoreApi must be used within RunStoreProvider");
  return store;
}
