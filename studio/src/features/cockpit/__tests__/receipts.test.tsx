import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import type { RawSignal, SignalType } from "../../../lib/runEvents.gen";
import { clock, lastHumanAction, overseerWakeFor, receiptFor } from "../receipts";
import { MessageWorker } from "../MessageWorker";
import { AnswerReceipt } from "../AnswerReceipt";
import { WakeOverseer } from "../../overseer/WakeOverseer";

let n = 0;
const sig = (type: SignalType, from: string | null, summary = type.toLowerCase(), detail: unknown = null, id?: string): RawSignal => ({
  id: id ?? `s${++n}`, type, summary, from_agent: from, priority: "normal", iteration: 1,
  detail: detail === null ? null : typeof detail === "string" ? detail : JSON.stringify(detail),
  timestamp: `2026-01-01T00:00:${String(++n % 60).padStart(2, "0")}`,
});
const typed = (raws: RawSignal[]) => {
  const store = createRunStore("r1");
  store.getState().ingest(raws);
  return store;
};

afterEach(() => vi.unstubAllGlobals());

describe("receipt model", () => {
  it("walks sent → idle/waking/queued → reading → got → answered from real signals", () => {
    const msg = sig("USER_COMMENT_PRIORITY", null, "Fix the score", { to: "dev", wake: true }, "m1");
    expect(receiptFor([], "m1", "dev").stage).toBe("sent");
    let s = typed([msg]).getState().signals;
    expect(receiptFor(s, "m1", "dev").stage).toBe("idle");
    expect(receiptFor(s, "m1", "dev", { wake: true }).stage).toBe("waking");
    expect(receiptFor(s, "m1", "dev", { running: true }).stage).toBe("queued");
    s = typed([msg, sig("STARTED", "dev")]).getState().signals;
    expect(receiptFor(s, "m1", "dev").stage).toBe("reading");
    s = typed([msg, sig("STARTED", "dev"), sig("DELIVERED", "dev", "x", { kind: "comment", ref: "m1", role: "dev" })]).getState().signals;
    expect(receiptFor(s, "m1", "dev").stage).toBe("got");
    s = typed([msg, sig("STARTED", "dev"), sig("DELIVERED", "dev", "x", { kind: "comment", ref: "m1", role: "dev" }),
      sig("AGENT_RESULT", "dev", "dev: fixed the score")]).getState().signals;
    expect(receiptFor(s, "m1", "dev")).toEqual({ stage: "answered", reply: "dev: fixed the score" });
  });

  it("follows an overseer wake to its decision", () => {
    const esc = sig("ESCALATION", "owner", "owner: look", null, "w1");
    expect(overseerWakeFor(typed([esc]).getState().signals, "w1").stage).toBe("waiting");
    const s = typed([esc, sig("OVERSEER_WAKE", "overseer"), sig("STARTED", "overseer"),
      sig("OVERSEER_INTERVENTION", "overseer", "rebrief dev")]).getState().signals;
    expect(overseerWakeFor(s, "w1")).toEqual({ stage: "decided", detail: "rebrief dev" });
  });

  it("confirms an answer until the asker carries on, and a sent-back plan until re-planned", () => {
    let s = typed([sig("HUMAN_NEEDED", "dev", "which db?"), sig("HUMAN_RESPONDED", null, "sqlite", { answer: "sqlite" })]).getState().signals;
    expect(lastHumanAction(s)).toEqual({ text: "You answered: “sqlite” · dev is resuming", settled: false });
    s = typed([sig("HUMAN_NEEDED", "dev", "which db?"), sig("HUMAN_RESPONDED", null, "sqlite", { answer: "sqlite" }),
      sig("TOOL_CALL", "dev")]).getState().signals;
    expect(lastHumanAction(s)?.settled).toBe(true);
    s = typed([sig("HUMAN_NEEDED", "planner", "Review the plan", { kind: "plan_review" }),
      sig("HUMAN_RESPONDED", null, "x", { answer: "one module", plan_review: "sent_back" })]).getState().signals;
    expect(lastHumanAction(s)?.text).toMatch(/planner is planning again with your note/);
    expect(clock(65)).toBe("1:05");
  });
});

describe("live receipts in the UI", () => {
  it("the worker message box shows the message's stage as signals arrive", async () => {
    localStorage.setItem("guudflow.token", "tok");
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ ok: true, signal_id: "m9" }) }) as unknown as Response));
    const store = createRunStore("r1");
    render(<RunStoreProvider store={store}><MessageWorker runId="r1" role="dev" name="Juno" live /></RunStoreProvider>);
    fireEvent.change(screen.getByLabelText("Message Juno"), { target: { value: "Fix the score" } });
    fireEvent.click(screen.getByRole("button", { name: "Send and wake" }));
    expect(screen.getByLabelText("Message Juno")).toHaveValue("");          // clears at once
    await waitFor(() => expect(screen.getByTestId("receipt-m9")).toHaveAttribute("data-stage", "sent"));
    act(() => store.getState().ingest([sig("USER_COMMENT_PRIORITY", null, "Fix the score", { to: "dev", wake: true }, "m9")]));
    expect(screen.getByTestId("receipt-m9")).toHaveTextContent("waking Juno");
    act(() => store.getState().ingest([sig("STARTED", "dev"), sig("DELIVERED", "dev", "x", { kind: "comment", ref: "m9", role: "dev" })]));
    expect(screen.getByTestId("receipt-m9")).toHaveTextContent("Juno has it");
  });

  it("the overseer wake shows waiting, then looking, then the decision", async () => {
    localStorage.setItem("guudflow.token", "tok");
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ ok: true, signal_id: "w2" }) }) as unknown as Response));
    const store = createRunStore("r1");
    render(<RunStoreProvider store={store}><WakeOverseer runId="r1" present active /></RunStoreProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Wake overseer" }));
    await waitFor(() => expect(screen.getByTestId("wake-state")).toHaveAttribute("data-stage", "sent"));
    act(() => store.getState().ingest([sig("ESCALATION", "owner", "owner: look", null, "w2"), sig("STARTED", "overseer")]));
    expect(screen.getByTestId("wake-state")).toHaveTextContent("The overseer is looking");
    act(() => store.getState().ingest([sig("OVERSEER_INTERVENTION", "overseer", "split T3")]));
    expect(screen.getByTestId("wake-state")).toHaveTextContent("The overseer decided: split T3");
  });

  it("confirms an answer in the run view", () => {
    const store = typed([sig("STARTED", "dev"), sig("HUMAN_NEEDED", "dev", "which db?"),
      sig("HUMAN_RESPONDED", null, "sqlite", { answer: "sqlite" })]);
    render(<RunStoreProvider store={store}><AnswerReceipt /></RunStoreProvider>);
    expect(screen.getByTestId("answer-receipt")).toHaveTextContent("You answered: “sqlite” · dev is resuming");
  });
});
