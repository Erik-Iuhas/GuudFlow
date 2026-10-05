import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import type { RawSignal, SignalType, TaskThreadDetail } from "../../../lib/runEvents.gen";
import { createRunStore, type RunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import type { CardWire } from "../../../types";
import { TaskBoard } from "../TaskBoard";

export function card(partial: Partial<CardWire> & { id: string }): CardWire {
  return {
    title: `Card ${partial.id}`,
    owner: "dev",
    column: "todo",
    kind: "code",
    size: "S",
    priority: 0,
    review_round: 0,
    acceptance: [`${partial.id} works`],
    depends_on: [],
    column_since: null,
    thread: [],
    ...partial,
  };
}

export function thread(
  task_id: string,
  kind: TaskThreadDetail["kind"],
  text: string,
  author = "dev",
  data: Record<string, unknown> = {},
): TaskThreadDetail {
  return { task_id, kind, author, text, iteration: 0, data };
}

let seq = 0;
export function signal(type: SignalType, detail: unknown, extra: Partial<RawSignal> = {}): RawSignal {
  seq += 1;
  return {
    id: `s${seq}`,
    type,
    summary: "",
    detail: typeof detail === "string" || detail === null ? detail : JSON.stringify(detail),
    from_agent: "dev",
    priority: "normal",
    iteration: 0,
    timestamp: new Date().toISOString(),
    ...extra,
  };
}

export interface Call {
  method: string;
  url: string;
  body: unknown;
  auth: string | null;
}

export interface Backend {
  calls: Call[];
  /** Calls that were not the initial GET /cards. */
  writes: () => Call[];
}

type Handler = (call: Call) => Response | undefined;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

/** Answer PATCH /cards/{id} the way the backend does: the card with its new priority. */
export function echoPatch(c: Call): Response | undefined {
  if (c.method !== "PATCH") return undefined;
  const id = c.url.split("/").pop() as string;
  return json(card({ id, owner: "dev", priority: (c.body as { priority: number }).priority }));
}

/** Stub `fetch`: GET /cards returns `cards`; `handler` can answer anything else. */
export function mockBackend(cards: CardWire[], handler: Handler = () => undefined): Backend {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const call: Call = {
        method: init?.method ?? "GET",
        url,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
        auth: headers["Authorization"] ?? null,
      };
      calls.push(call);
      const custom = handler(call);
      if (custom) return custom;
      if (call.method === "GET" && url.endsWith("/cards")) return json({ cards });
      return json({ detail: "unexpected call" }, 500);
    }),
  );
  return { calls, writes: () => calls.filter((c) => c.method !== "GET") };
}

export function renderBoard(opts: { store?: RunStore; reviewCap?: number } = {}) {
  const store = opts.store ?? createRunStore("r1");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <RunStoreProvider store={store}>
        <TaskBoard runId="r1" reviewCap={opts.reviewCap} />
      </RunStoreProvider>
    </QueryClientProvider>,
  );
  return { store, client, ...utils };
}
