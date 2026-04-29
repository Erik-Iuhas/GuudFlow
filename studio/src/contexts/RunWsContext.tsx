import { createContext, useContext } from "react";
import type { Signal } from "../types";

type SignalHandler = (signal: Signal) => void;
type Unsubscribe = () => void;

export interface RunWsSubscriber {
  subscribe: (handler: SignalHandler) => Unsubscribe;
}

const RunWsContext = createContext<RunWsSubscriber | null>(null);

export function RunWsProvider({
  value,
  children,
}: {
  value: RunWsSubscriber;
  children?: React.ReactNode;
}) {
  return <RunWsContext.Provider value={value}>{children}</RunWsContext.Provider>;
}

export function useRunWs(): RunWsSubscriber | null {
  return useContext(RunWsContext);
}
