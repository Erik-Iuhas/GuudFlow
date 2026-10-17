import { createContext, useCallback, useContext, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import type { OwnerContextWire } from "../plan/api";

// Where the owner is, for the Jack dock (docs/JACK_DOCK_PLAN.md J2). Pages register what they
// show; the dock sends it with a message as ids only, and the host resolves them.

export interface JackPlace extends OwnerContextWire {
  projectId?: string;
}

interface JackState {
  /** The innermost registered place (a run page, then the card drawer open on it). */
  place: JackPlace;
  push: (key: string, p: JackPlace) => void;
  pop: (key: string) => void;
  open: boolean;
  setOpen: (open: boolean) => void;
  /** A starter question from an "Ask Jack" button. */
  draft: string;
  ask: (question: string, place?: JackPlace) => void;
  clearDraft: () => void;
}

const DEFAULT: JackPlace = { page: "other" };
const Ctx = createContext<JackState | null>(null);

export function JackProvider({ children }: { children: ReactNode }) {
  const [stack, setStack] = useState<[string, JackPlace][]>([]);
  const [override, setOverride] = useState<JackPlace | null>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const push = useCallback((key: string, p: JackPlace) => {
    setStack((s) => [...s.filter(([k]) => k !== key), [key, p]]);
    setOverride(null);
  }, []);
  const pop = useCallback((key: string) => setStack((s) => s.filter(([k]) => k !== key)), []);
  const ask = useCallback((question: string, p?: JackPlace) => {
    if (p) setOverride(p);
    setDraft(question);
    setOpen(true);
  }, []);
  const place = override ?? stack[stack.length - 1]?.[1] ?? DEFAULT;
  const value = useMemo(() => ({ place, push, pop, open, setOpen, draft, ask, clearDraft: () => setDraft("") }),
    [place, push, pop, open, draft, ask]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** The dock's state; outside a provider (tests of single components) a no-op stand-in. */
export function useJack(): JackState {
  return useContext(Ctx) ?? {
    place: DEFAULT, push: () => {}, pop: () => {}, open: false, setOpen: () => {}, draft: "",
    ask: () => {}, clearDraft: () => {},
  };
}

/** A page says what it shows while it is mounted. */
export function useJackPlace(place: JackPlace | null): void {
  const { push, pop } = useJack();
  const id = useId();
  const key = JSON.stringify(place);
  useEffect(() => {
    if (!place) return;
    push(id, place);
    return () => pop(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, id, push, pop]);
}

/** The chip text for a place: "Run · run_x · T3", "Spec · R1". */
export function placeLabel(p: OwnerContextWire): string {
  const head = p.page === "run" ? "Run" : p.page.charAt(0).toUpperCase() + p.page.slice(1);
  const bits = [p.run_id, p.card, p.item, p.requirement, p.component].filter(Boolean);
  return [head, ...bits].join(" · ");
}
