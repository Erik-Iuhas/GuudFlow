import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createRunStore, type RunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import { TooltipProvider } from "../../../components/ui/tooltip";
import type { RawSignal, SignalType } from "../../../lib/runEvents.gen";
import { AgentDetail } from "../AgentDetail";
import { lastEditId } from "../RoleChanges";

let n = 0;
function sig(type: SignalType, from: string | null, detail: unknown = null): RawSignal {
  n += 1;
  return {
    id: `c${n}`, type, summary: type.toLowerCase(), from_agent: from, priority: "normal", iteration: 1,
    detail: detail === null ? null : JSON.stringify(detail),
    timestamp: `2026-03-01T10:00:${String(n % 60).padStart(2, "0")}+00:00`,
  };
}
const tool = (name: string) => sig("TOOL_CALL", "dev", { tool: name, status: "completed", input: "{}", duration_ms: 1 });

const DIFF = [
  "diff --git a/src/form.ts b/src/form.ts",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/src/form.ts",
  "@@ -0,0 +1 @@",
  "+export const form = 1;",
  "",
].join("\n");
const CHANGES = {
  role: "dev", base: "a", head: "b", truncated: false, diff: DIFF,
  files: [{ path: "src/form.ts", status: "untracked", added: 1, removed: 0 }],
};

function wrap(store: RunStore, ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <RunStoreProvider store={store}>{ui}</RunStoreProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function runningDev(): RunStore {
  const store = createRunStore("r1");
  store.getState().ingest([sig("STARTED", "dev"), tool("read"), tool("edit")]);
  return store;
}

describe("lastEditId", () => {
  it("picks the latest file-changing tool call", () => {
    const store = createRunStore("r1");
    store.getState().ingest([tool("write"), tool("read"), tool("grep")]);
    const feed = store.getState().toolFeed.dev;
    expect(lastEditId(feed)).toBe(feed[0].id);
    expect(lastEditId(undefined)).toBe("");
  });
});

describe("AgentDetail Changes tab", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response(JSON.stringify(CHANGES), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("shows the role's files and the rendered diff", async () => {
    wrap(runningDev(), <AgentDetail role="dev" />);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Changes" }));
    const panel = await screen.findByTestId("role-changes-dev");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/runs/r1/roles/dev/changes");
    expect(panel).toHaveTextContent("1 file +1 −0");
    expect(within(panel).getByText("new")).toBeInTheDocument();
    expect(within(panel).getByTestId("diff-viewer")).toHaveTextContent("export const form = 1;");
  });

  it("refreshes after the role edits a file, not after a read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const store = runningDev();
    wrap(store, <AgentDetail role="dev" />);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Changes" }));
    await screen.findByTestId("role-changes-dev");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    act(() => store.getState().ingest(tool("read")));
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    act(() => store.getState().ingest(tool("write")));
    await act(async () => { await vi.advanceTimersByTimeAsync(2_500); });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("says so when the role has no checkout yet", async () => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ detail: "no checkout" }), { status: 404 }));
    wrap(runningDev(), <AgentDetail role="dev" />);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Changes" }));
    expect(await screen.findByText(/No working copy to show for dev yet/)).toBeInTheDocument();
  });
});
