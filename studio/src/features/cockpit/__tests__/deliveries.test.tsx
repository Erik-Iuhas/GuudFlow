import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import type { RawSignal, SignalType } from "../../../lib/runEvents.gen";
import { Deliveries } from "../Deliveries";
import { CommentBox } from "../../../components/CommentBox";

let n = 0;
const sig = (type: SignalType, summary: string, detail: unknown = null, from: string | null = null): RawSignal => ({
  id: `s${++n}`, type, summary, from_agent: from, priority: "normal", iteration: 1,
  detail: detail === null ? null : JSON.stringify(detail), timestamp: `2026-01-01T00:00:${String(n).padStart(2, "0")}`,
});

afterEach(() => vi.unstubAllGlobals());

describe("who has been told", () => {
  it("lists comments and project changes with who got them and who is waiting", () => {
    const store = createRunStore("r1");
    const comment = sig("USER_COMMENT", "Use snake_case");
    const direct = sig("USER_COMMENT_PRIORITY", "Fix the score", { to: "dev", wake: false, text: "Fix the score" });
    store.getState().ingest([
      comment, direct,
      sig("PROJECT_UPDATED", "project changed", { update: "U1", changes: ["Decision D1 Window: 800x600"] }, "runner"),
      sig("DELIVERED", "x", { kind: "comment", ref: comment.id, role: "dev" }, "dev"),
      sig("DELIVERED", "x", { kind: "project_update", ref: "U1", role: "tester" }, "tester"),
    ]);
    render(<RunStoreProvider store={store}><Deliveries workers={["dev", "tester"]} /></RunStoreProvider>);
    const all = screen.getByTestId(`delivery-${comment.id}`);
    expect(within(all).getByText("✓ dev")).toBeInTheDocument();
    expect(within(all).getByText("waiting: tester")).toBeInTheDocument();
    const one = screen.getByTestId(`delivery-${direct.id}`);
    expect(within(one).getByText(/To dev/)).toBeInTheDocument();
    expect(within(one).getByText(/dev reads it when it next starts/)).toBeInTheDocument();
    const update = screen.getByTestId("delivery-U1");
    expect(within(update).getByText(/800x600/)).toBeInTheDocument();
    expect(within(update).getByText("✓ tester")).toBeInTheDocument();
  });

  it("sends to one worker when one is picked", async () => {
    localStorage.setItem("guudflow.token", "tok");
    const fetch = vi.fn(async () => ({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }), json: async () => ({ ok: true }) }) as unknown as Response);
    vi.stubGlobal("fetch", fetch);
    render(<CommentBox runId="r1" roles={["dev", "tester"]} names={{ dev: "Juno" }} />);
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "dev" } });
    expect(screen.getByText("Juno")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Comment for the agents"), { target: { value: "Fix the score" } });
    fireEvent.click(screen.getByRole("button", { name: "Post" }));
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const calls = fetch.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe("/api/runs/r1/roles/dev/message");
    expect(JSON.parse(String(calls[0][1].body))).toMatchObject({ text: "Fix the score", wake: false });
  });
});
