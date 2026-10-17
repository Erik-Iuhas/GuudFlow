import { render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import type { RawSignal } from "../../../lib/runEvents.gen";
import type { RunStore } from "../../../stores/runEvents";

const feed: RawSignal[] = [];
vi.mock("../../../hooks/useRunSocket", () => ({
  useRunSocket: (_runId: string, store: RunStore) => {
    useEffect(() => { store.getState().ingest(feed); }, [store]);
    return { status: "open", error: null, retry: () => {} };
  },
}));

import { LiveReceipt } from "../LiveReceipt";

const sig = (id: string, type: RawSignal["type"], from: string | null, detail: unknown = null): RawSignal => ({
  id, type, summary: type.toLowerCase(), from_agent: from, priority: "normal", iteration: 1,
  detail: detail === null ? null : JSON.stringify(detail), timestamp: `2026-01-01T00:00:0${id.length}`,
});

describe("LiveReceipt", () => {
  it("follows a dock message on the run's own feed", async () => {
    feed.splice(0, feed.length,
      sig("m1", "USER_COMMENT_PRIORITY", null, { to: "dev", wake: false }),
      sig("s2", "STARTED", "dev"),
      sig("d3", "DELIVERED", "dev", { kind: "comment", ref: "m1", role: "dev" }));
    render(<LiveReceipt runId="run_x" kind="message" refId="m1" role="dev" at={Date.now()} />);
    expect(await screen.findByTestId("receipt-m1")).toHaveTextContent("dev has it");
  });
});
